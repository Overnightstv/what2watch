"""PA forward schedule fetch + BARB ID matching.

Fetches tonight's primetime schedule from the PA TV API and resolves
each show to a BARB series_id via IdMatcher. Unmatched items are
excluded from selection (never guessed — spec 3.1).
"""
from __future__ import annotations

import os
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

from overnight.matching.id_match import IdMatcher
from overnight.models import EpisodeRecord, ScheduleItem

load_dotenv(Path(__file__).parents[3] / ".env")

PA_BASE = os.getenv("PA_TV_BASE_URL", "https://tv.api.pressassociation.io/v2")

# PA channel UUIDs for the five main channels
PA_CHANNEL_IDS: dict[str, str] = {
    "BBC1":      "78bd54a7-6883-575e-a84f-f328dc424979",
    "BBC2":      "7a6cd877-f7c0-5d40-8862-0ad24318b712",
    "ITV1":      "f0eac74d-2245-57c9-9dfa-d8a54da0c066",
    "Channel 4": "fd66055a-5bc7-51e8-8421-df88e60c98b3",
    "Channel 5": "bbc2a081-fc85-5c05-8ff0-4438cc97de2c",
}

# Default catchup platforms per channel
CATCHUP: dict[str, str] = {
    "BBC1":      "BBC iPlayer",
    "BBC2":      "BBC iPlayer",
    "ITV1":      "ITVX",
    "Channel 4": "Channel 4",
    "Channel 5": "My5",
}

LIVE_EVENT_RE = re.compile(
    r"\b(live|final|grand final|cup|semi.final|election|awards|ceremony)\b", re.I
)

PRIMETIME_START = 18 * 60
PRIMETIME_END   = 23 * 60

SKIP_RE = re.compile(
    r"^(News|Weather|Newsnight|Question Time|Panorama|Breakfast|"
    r"Good Morning|Loose Women|This Morning|Sign Zone|CBeebies|CBBC|"
    r"Close|Test Card|Regional|Local|Junction|Presentation|"
    r"EastEnders|Coronation Street|Emmerdale|Hollyoaks|"
    r"Match of the Day|Football|Rugby|Cricket|Sport)",
    re.I,
)


def _pa_headers() -> dict:
    key = os.getenv("PA_TV_API_KEY", "")
    return {"apikey": key} if key else {}


def _best_image(asset: dict) -> str | None:
    for m in asset.get("media", []):
        if "image" in m.get("kind", ""):
            href = m.get("rendition", {}).get("default", {}).get("href")
            if href:
                return href
    return None


def _extract_image(item: dict) -> str | None:
    asset = item.get("asset", {})
    # Prefer series/season image over episode still
    for rel in asset.get("related", []):
        img = _best_image(rel)
        if img:
            return img
    return _best_image(asset)


def _parse_tx(raw: str) -> datetime | None:
    """Parse ISO8601 TX time from PA, return UTC datetime."""
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None


def fetch_pa_schedule(tx_date: date) -> list[dict]:
    """Fetch primetime schedule items from PA for a given date (all 5 channels)."""
    headers = _pa_headers()
    if not headers:
        print("  [pa] No PA_TV_API_KEY — skipping schedule fetch")
        return []

    # 17:00–23:30 BST = 16:00–22:30 UTC (conservative window covering GMT and BST)
    start = f"{tx_date.isoformat()}T16:00:00Z"
    end   = f"{tx_date.isoformat()}T23:30:00Z"

    all_items = []
    for channel, channel_id in PA_CHANNEL_IDS.items():
        try:
            resp = requests.get(
                f"{PA_BASE}/schedule",
                params={"channelId": channel_id, "start": start, "end": end},
                headers=headers,
                timeout=15,
            )
            if resp.status_code != 200:
                print(f"  [pa] {channel} schedule HTTP {resp.status_code}")
                continue
            for item in resp.json().get("item", []):
                item["_channel"] = channel
                all_items.append(item)
        except Exception as exc:
            print(f"  [pa] {channel} fetch failed: {exc}")

    return all_items


def build_barb_index(universe: dict[str, list[EpisodeRecord]]) -> list[dict]:
    """Build the IdMatcher index from the ingested BARB universe."""
    index = []
    for sid, eps in universe.items():
        latest = eps[-1]
        try:
            h, m = latest.slot_start.split(":")
            tx_dt = datetime(
                latest.tx_date.year, latest.tx_date.month, latest.tx_date.day,
                int(h), int(m), tzinfo=timezone.utc
            )
        except Exception:
            tx_dt = None
        index.append({
            "series_id": sid,
            "title":     latest.title,
            "channel":   latest.channel,
            "tx":        tx_dt,
        })
    return index


def build_schedule(
    tx_date: date,
    universe: dict[str, list[EpisodeRecord]],
    lookahead_days: int = 7,
) -> list[ScheduleItem]:
    """
    Fetch PA schedule for the next `lookahead_days` days and resolve
    each show to a BARB series_id. Returns list[ScheduleItem].
    Unmatched shows have series_id=None and will be excluded by the engine.
    """
    barb_index = build_barb_index(universe)
    matcher = IdMatcher(barb_index)

    schedule: list[ScheduleItem] = []
    unmatched_log: list[str] = []

    for offset in range(lookahead_days):
        check_date = tx_date + timedelta(days=offset)
        raw_items  = fetch_pa_schedule(check_date)

        for item in raw_items:
            channel = item.get("_channel", "")
            title   = (item.get("title") or "").strip()

            if not title or SKIP_RE.match(title):
                continue

            tx_raw = item.get("startTime") or item.get("start") or ""
            tx_dt  = _parse_tx(tx_raw)
            if tx_dt is None:
                continue

            tx_mins = tx_dt.hour * 60 + tx_dt.minute
            if not (PRIMETIME_START <= tx_mins < PRIMETIME_END):
                continue

            # Resolve BARB series_id
            pa_id = item.get("id", "")
            genre = item.get("genre") or item.get("type") or ""
            sid, method = matcher.match(pa_id, title, channel, tx_dt)

            if sid is None:
                unmatched_log.append(f"{check_date} {channel}: {title}")

            image_ref = _extract_image(item)
            is_live   = bool(LIVE_EVENT_RE.search(title)) or bool(
                item.get("flags", {}).get("live")
            )

            schedule.append(ScheduleItem(
                pa_id        = pa_id,
                series_id    = sid,
                title        = title,
                channel      = channel,
                tx           = tx_dt,
                genre        = genre,
                is_live_event= is_live,
                availability = [CATCHUP.get(channel, "")],
                image_ref    = image_ref,
                is_new_series= bool(item.get("flags", {}).get("newSeries")),
            ))

    matched = sum(1 for s in schedule if s.series_id)
    total   = len(schedule)
    print(f"  [pa] {total} primetime items, {matched} matched "
          f"({100*matched//total if total else 0}%)")
    if unmatched_log:
        print(f"  [pa] Unmatched ({len(unmatched_log)}): "
              + ", ".join(unmatched_log[:5])
              + ("…" if len(unmatched_log) > 5 else ""))

    return schedule
