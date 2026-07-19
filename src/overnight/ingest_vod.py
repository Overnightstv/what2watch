"""VOD ingest — autonomous discovery from BARB daily streaming chart.

Primary source: GET /api/vod-report/{date}?token=...
Returns the complete BARB UK streaming dataset (~1000 items/day) covering every
show BARB recorded on any streaming platform that day — Netflix, Prime Video,
Disney+, BBC iPlayer, ITVX, All 4, My5, and more.

No seed lists needed: Clarkson's Farm, Netflix originals, and every other
streaming-native title appear autonomously if UK audiences watched them.

Ingest strategy:
  - Fetch VOD report for each day in the trailing window (skips days already cached)
  - Parse "Show Title: Series N, Episode N" → series name + aggregate episodes
  - Store one VodRecord per (series, date) with daily viewer totals
  - Prune records older than the retention window
"""
from __future__ import annotations

import os
import re
import time
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

from overnight.models import EpisodeRecord, VodRecord

load_dotenv(Path(__file__).parents[2] / ".env")

BASE_URL = os.getenv("OVERNIGHTS_BASE_URL", "https://api.on-tv.tech/api")
# VOD report uses token as query param, not Bearer header
_JWT = os.getenv("OVERNIGHTS_PROGRAMME_TOKEN", os.getenv("OVERNIGHTS_API_TOKEN", ""))
_JWT = _JWT.replace("Bearer ", "").strip()

# Platform display-name normalisation (VOD report names → user-facing names)
_PLATFORM_DISPLAY: dict[str, str] = {
    "4+": "All 4",
    "5": "My5",
    "Now": "Now",
    "U": "UKTV Play",
}

# Minimum viewer count to include a series (filters out noise at the bottom of the chart)
_MIN_VIEWERS = 5_000

# How many days to retain VOD records
_VOD_WINDOW_DAYS = 14


def _series_slug(title: str, provider: str) -> str:
    slug = re.sub(r"\s+", "-", re.sub(r"[^\w\s]", "", title.lower())).strip("-")
    plat = re.sub(r"[^\w]", "", provider.lower())
    return f"vod-{plat}-{slug}"


def _parse_series_name(programme_name: str) -> str:
    """Extract series title from BARB programme name format.

    Handles patterns like:
      "Clarkson's Farm: Series 5, Episode 8"    → "Clarkson's Farm"
      "Ride or Die, Series 1, Episode 1"        → "Ride or Die"
      "CORONATION STREET, Series 67, Episode 140" → "Coronation Street"
      "FIFA World Cup 2026: Series 13"           → "FIFA World Cup 2026"
      "FILM: Project Hail Mary (2026)"           → "Project Hail Mary (2026)"
    """
    # Strip leading "FILM: " prefix
    name = re.sub(r"^FILM:\s+", "", programme_name, flags=re.IGNORECASE)
    # Strip "[,: ] Series N, Episode N" — supports both colon and comma separators
    name = re.sub(r"\s*[,:]\s+Series\s+\S+,?\s+Episode\s+\d+$", "", name, flags=re.IGNORECASE).strip()
    # Strip remaining "[,: ] Series N" suffix (no episode component)
    name = re.sub(r"\s*[,:]\s+Series\s+\S+$", "", name, flags=re.IGNORECASE).strip()
    # Normalise all-caps names (e.g. "CORONATION STREET" → "Coronation Street")
    if name and name.upper() == name:
        name = name.title()
    return name


def fetch_vod_report_raw(activity_date: date, retries: int = 2) -> list[dict]:
    """GET /api/vod-report/{date}?token=... — full BARB streaming dataset."""
    url = f"{BASE_URL}/vod-report/{activity_date.isoformat()}?token={_JWT}"
    for attempt in range(retries + 1):
        try:
            resp = requests.get(url, timeout=60)
            resp.raise_for_status()
            data = resp.json().get("data") or {}
            content = data.get("content") or {}
            return content.get("vodResults", [])
        except Exception as exc:
            if attempt < retries:
                time.sleep(2)
            else:
                raise exc
    return []


def ingest_vod_incremental(
    existing: dict[str, list[VodRecord]],
    universe: dict[str, list[EpisodeRecord]] | None = None,
    days: int = _VOD_WINDOW_DAYS,
    today: date | None = None,
) -> dict[str, list[VodRecord]]:
    """Build / refresh the VOD universe from BARB daily streaming reports.

    Fetches the BARB VOD chart for each day in the trailing window.
    Skips dates already present in the cache (idempotent).
    The `universe` parameter is accepted for pipeline compatibility but is no
    longer used — all streaming shows are discovered autonomously from the chart.
    """
    today = today or date.today()
    cutoff = today - timedelta(days=days)

    # Find which dates we already have in the cache
    dates_cached: set[date] = set()
    for recs in existing.values():
        for r in recs:
            dates_cached.add(r.date_of_activity)

    # Dates to fetch (most recent first)
    dates_to_fetch = [
        today - timedelta(days=i)
        for i in range(days)
        if (today - timedelta(days=i)) not in dates_cached
        and (today - timedelta(days=i)) >= cutoff
    ]

    if not dates_to_fetch:
        print("  [vod] Cache is current — nothing to fetch")
    else:
        print(f"  [vod] Fetching VOD reports for {len(dates_to_fetch)} date(s)")

    new_series_count = 0
    for activity_date in dates_to_fetch:
        try:
            raw = fetch_vod_report_raw(activity_date)
        except Exception as exc:
            print(f"    [vod] {activity_date}: fetch failed — {exc}")
            continue

        if not raw:
            print(f"    [vod] {activity_date}: no data returned")
            continue

        # Aggregate episodes → series for this date
        agg: dict[tuple[str, str], dict] = defaultdict(
            lambda: {"viewers": 0, "minutes": 0, "genre": ""}
        )
        for item in raw:
            programme_name = item.get("programmeName") or ""
            platform_raw = item.get("station", {}).get("name", "")
            platform = _PLATFORM_DISPLAY.get(platform_raw, platform_raw)
            viewers = int(item.get("viewers") or 0)
            minutes = int(item.get("viewerMinutes") or 0)
            genre = item.get("genre", "")

            if not programme_name or not platform or viewers <= 0:
                continue

            series_name = _parse_series_name(programme_name)
            key = (series_name, platform)
            agg[key]["viewers"] += viewers
            agg[key]["minutes"] += minutes
            if not agg[key]["genre"]:
                agg[key]["genre"] = genre

        date_count = 0
        for (series_name, platform), totals in agg.items():
            if totals["viewers"] < _MIN_VIEWERS:
                continue
            sid = _series_slug(series_name, platform)
            rec = VodRecord(
                series_id=sid,
                title=series_name,
                platform=platform,
                date_of_activity=activity_date,
                views=totals["viewers"],
                minutes_watched=totals["minutes"],
                genre=totals["genre"],
            )
            existing.setdefault(sid, []).append(rec)
            date_count += 1

        new_series_count += date_count
        print(f"    [vod] {activity_date}: {len(raw)} raw items → {date_count} series records")
        time.sleep(0.5)  # polite rate-limiting

    # Prune records outside the retention window and drop empty series
    for sid in list(existing.keys()):
        existing[sid] = sorted(
            (r for r in existing[sid] if r.date_of_activity >= cutoff),
            key=lambda r: r.date_of_activity,
        )
        if not existing[sid]:
            del existing[sid]

    total_recs = sum(len(v) for v in existing.values())
    print(
        f"  [vod] Universe: {len(existing)} streaming series, "
        f"{total_recs} daily snapshots"
    )
    return existing
