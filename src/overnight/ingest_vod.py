"""VOD ingest — fetches BARB streaming data via api.on-tv.tech content endpoints.

Discovery strategy:
  1. Top linear shows (from the BARB universe) that air on channels with catch-up
     services — BBC→iPlayer, ITV→ITVX, C4→All 4, C5→My5
  2. Curated streaming-native seeds (Netflix/Prime Video/Disney+ originals)

For each candidate:
  GET /content/search?keyword={title}   — find the show's BARB content IDs
  POST /content/viewers                  — 7-day viewer total per episode

The viewer total for a 7-day window gives a "currently being watched" signal.
Stored as one VodRecord per day so consecutive days can be compared for trend.
"""
from __future__ import annotations

import os
import re
import time
from datetime import date, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

from overnight.models import EpisodeRecord, VodRecord

load_dotenv(Path(__file__).parents[2] / ".env")

BASE_URL = os.getenv("OVERNIGHTS_BASE_URL", "https://api.on-tv.tech/api")
_TOKEN   = os.getenv("OVERNIGHTS_PROGRAMME_TOKEN", os.getenv("OVERNIGHTS_API_TOKEN", ""))
if not _TOKEN.startswith("Bearer "):
    _TOKEN = f"Bearer {_TOKEN}"
_HEADERS = {"Authorization": _TOKEN, "Accept": "application/json"}

# Channels whose shows are available on catch-up BVOD
_CATCH_UP_CHANNELS: dict[str, str] = {
    "BBC One":     "BBC iPlayer",
    "BBC Two":     "BBC iPlayer",
    "BBC Three":   "BBC iPlayer",
    "BBC Four":    "BBC iPlayer",
    "ITV":         "ITVX",
    "ITV2":        "ITVX",
    "ITV3":        "ITVX",
    "ITV4":        "ITVX",
    "Channel 4":   "All 4",
    "E4":          "All 4",
    "More4":       "All 4",
    "Film4":       "All 4",
    "Channel 5":   "My5",
    "5STAR":       "My5",
}

# Streaming-native seeds — popular shows on Netflix/Prime/Disney+ not in the
# linear universe. Update this list as new shows launch.
STREAMING_SEEDS: list[tuple[str, str]] = [
    # (title, platform hint for display only)
    ("Stranger Things",    "Netflix"),
    ("Bridgerton",         "Netflix"),
    ("The Crown",          "Netflix"),
    ("Black Mirror",       "Netflix"),
    ("Wednesday",          "Netflix"),
    ("Squid Game",         "Netflix"),
    ("The Diplomat",       "Netflix"),
    ("Adolescence",        "Netflix"),
    ("The Boys",           "Prime Video"),
    ("Rings of Power",     "Prime Video"),
    ("Citadel",            "Prime Video"),
    ("Fallout",            "Prime Video"),
    ("The Bear",           "Disney+"),
    ("Only Murders",       "Disney+"),
    ("Abbott Elementary",  "Disney+"),
    ("Andor",              "Disney+"),
    ("Loki",               "Disney+"),
    ("Slow Horses",        "Apple TV+"),
    ("Severance",          "Apple TV+"),
    ("Presumed Innocent",  "Apple TV+"),
]

# Window for "currently being watched" viewer count
_VOD_WINDOW_DAYS = 7

# Max linear shows to query (keeps runtime reasonable — pick by episode count)
_MAX_LINEAR_SHOWS = 40


def _series_slug(title: str, provider: str) -> str:
    slug = re.sub(r"\s+", "-", re.sub(r"[^\w\s]", "", title.lower())).strip("-")
    plat = re.sub(r"[^\w]", "", provider.lower())
    return f"vod-{plat}-{slug}"


def _search_content(title: str, retries: int = 2) -> list[dict]:
    """GET /content/search?keyword={title} — returns list of show objects."""
    for attempt in range(retries + 1):
        try:
            resp = requests.get(
                f"{BASE_URL}/content/search",
                params={"keyword": title},
                headers=_HEADERS,
                timeout=90,
            )
            resp.raise_for_status()
            return resp.json().get("data", [])
        except Exception as exc:
            if attempt < retries:
                time.sleep(2)
            else:
                print(f"    [vod] search '{title}': {exc}")
    return []


def _fetch_viewers(episode: dict, window_days: int) -> tuple[float, float, str]:
    """POST /content/viewers — returns (viewers, minutesWatched, providerName)."""
    today = date.today()
    payload = {
        "startDate": (today - timedelta(days=window_days)).isoformat(),
        "endDate":   today.isoformat(),
        "episode":   episode,
    }
    try:
        resp = requests.post(
            f"{BASE_URL}/content/viewers",
            json=payload,
            headers=_HEADERS,
            timeout=90,
        )
        resp.raise_for_status()
        d = resp.json().get("data", {})
        total     = d.get("total", {})
        providers = d.get("byProvider", [])
        provider  = providers[0].get("providerName", "") if providers else ""
        return float(total.get("viewers", 0)), float(total.get("viewerMinutes", 0)), provider
    except Exception as exc:
        print(f"    [vod] viewers: {exc}")
        return 0.0, 0.0, ""


def _best_episode(show: dict) -> dict | None:
    """Return the episode with the highest episodeId from the most recent series."""
    series_list = show.get("series", [])
    if not series_list:
        return None
    latest_series = max(series_list, key=lambda s: s.get("seasonId", 0))
    eps = latest_series.get("episodes", [])
    if not eps:
        return None
    return max(eps, key=lambda e: e.get("episodeId", 0))


def fetch_vod_for_title(
    title: str,
    activity_date: date,
    expected_provider: str = "",
) -> VodRecord | None:
    """Search for one title and return a VodRecord if viewers > 0."""
    results = _search_content(title)
    time.sleep(0.3)

    for result in results:
        ep = _best_episode(result)
        if not ep:
            continue
        viewers, minutes, provider = _fetch_viewers(ep, _VOD_WINDOW_DAYS)
        time.sleep(0.2)
        if viewers <= 0:
            continue

        # If we have an expected provider hint and the provider doesn't match, skip
        if expected_provider and provider and expected_provider.lower() not in provider.lower():
            continue

        show_title = (result.get("metaBroadcastContentName") or title).strip()
        genre      = ""  # genre not returned by this endpoint
        sid        = _series_slug(show_title, provider or expected_provider)

        return VodRecord(
            series_id        = sid,
            title            = show_title,
            platform         = provider or expected_provider,
            date_of_activity = activity_date,
            views            = int(viewers),
            minutes_watched  = int(minutes),
            genre            = genre,
        )
    return None


def ingest_vod_incremental(
    existing: dict[str, list[VodRecord]],
    universe: dict[str, list[EpisodeRecord]] | None = None,
    days: int = 14,
    today: date | None = None,
) -> dict[str, list[VodRecord]]:
    """Build / refresh the VOD universe.

    Skips series that already have a snapshot for today (idempotent).
    `universe` is the linear BARB universe — used to pick catch-up candidates.
    """
    today  = today or date.today()
    cutoff = today - timedelta(days=days)

    already_today = {
        sid for sid, recs in existing.items()
        if any(r.date_of_activity == today for r in recs)
    }

    candidates: list[tuple[str, str]] = []  # (title, platform_hint)

    # 1. Linear shows on catch-up channels (sorted by episode count as proxy for relevance)
    if universe:
        linear_picks: list[tuple[str, str, int]] = []
        for sid, eps in universe.items():
            latest  = eps[-1]
            channel = latest.channel
            if channel not in _CATCH_UP_CHANNELS:
                continue
            platform = _CATCH_UP_CHANNELS[channel]
            linear_picks.append((latest.title, platform, len(eps)))
        linear_picks.sort(key=lambda x: x[2], reverse=True)
        candidates.extend((t, p) for t, p, _ in linear_picks[:_MAX_LINEAR_SHOWS])

    # 2. Streaming-native seeds (Netflix / Prime / Disney+)
    candidates.extend(STREAMING_SEEDS)

    # Deduplicate by title (case-insensitive)
    seen_titles: set[str] = set()
    deduped: list[tuple[str, str]] = []
    for title, platform in candidates:
        key = title.lower()
        if key not in seen_titles:
            seen_titles.add(key)
            deduped.append((title, platform))

    print(f"  [vod] Checking {len(deduped)} titles ({len([c for c in deduped if c[1] in ('Netflix','Prime Video','Disney+','Apple TV+')])} streaming-native)")

    new_count = 0
    for title, platform in deduped:
        # Quick skip: if a record with this title already exists for today, skip
        slug = _series_slug(title, platform)
        if slug in already_today:
            continue

        rec = fetch_vod_for_title(title, today, expected_provider=platform)
        if rec:
            existing.setdefault(rec.series_id, []).append(rec)
            new_count += 1
            print(f"    [vod] {rec.title} ({rec.platform}): {rec.views:,} viewers")

    # Prune old records and drop empty series
    for sid in list(existing.keys()):
        existing[sid] = sorted(
            (r for r in existing[sid] if r.date_of_activity >= cutoff),
            key=lambda r: r.date_of_activity,
        )
        if not existing[sid]:
            del existing[sid]

    total = sum(len(v) for v in existing.values())
    print(f"  [vod] Universe: {len(existing)} streaming titles ({new_count} new today), {total} snapshots")
    return existing
