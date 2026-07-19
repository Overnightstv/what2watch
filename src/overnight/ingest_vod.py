"""VOD ingest — fetches BARB streaming data from system-next.on-tv.tech.

Returns {series_id: [VodRecord]} keyed by a stable slug.
Mirrors the incremental approach in ingest.py: load cache, fetch only
missing days, prune to rolling window.
"""
from __future__ import annotations

import os
import re
import time
from datetime import date, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

from overnight.models import VodRecord

load_dotenv(Path(__file__).parents[2] / ".env")

VOD_BASE_URL = os.getenv("VOD_BASE_URL", "https://system-next.on-tv.tech")
_RAW_TOKEN = os.getenv("VOD_TOKEN") or os.getenv("OVERNIGHTS_PROGRAMME_TOKEN", "")
VOD_TOKEN = _RAW_TOKEN.replace("Bearer ", "").strip()

# Platforms we care about (skip linear replay players like YouTube)
STREAMING_PLATFORMS = {
    "Netflix", "Prime Video", "Disney+", "Apple TV+",
    "NOW", "Paramount+", "ITVX", "iPlayer", "All 4", "My5",
    "BritBox", "Mubi", "Shudder",
}


def _vod_series_id(title: str, platform: str) -> str:
    slug = re.sub(r"\s+", "-", re.sub(r"[^\w\s]", "", title.lower())).strip("-")
    plat = re.sub(r"[^\w]", "", platform.lower())
    return f"vod-{plat}-{slug}"


def fetch_vod_day(activity_date: date) -> list[VodRecord]:
    """Fetch one day's VOD chart. Returns empty list on any error."""
    try:
        resp = requests.get(
            f"{VOD_BASE_URL}/vod-report",
            params={"dateOfActivity": activity_date.isoformat(), "token": VOD_TOKEN},
            headers={"Accept": "application/json"},
            timeout=30,
        )
        resp.raise_for_status()

        ct = resp.headers.get("content-type", "")
        if "html" in ct or resp.text.lstrip().startswith("<"):
            print(f"    [vod] {activity_date}: unexpected HTML response (API unreachable?)")
            return []

        data = resp.json()
        items = data if isinstance(data, list) else (
            data.get("data") or data.get("vodResults") or data.get("items") or []
        )

        records = []
        for item in items:
            title    = (item.get("title") or item.get("programmeName") or "").strip()
            platform = (item.get("player") or item.get("platform") or "").strip()
            if not title or not platform:
                continue
            if platform not in STREAMING_PLATFORMS:
                continue  # skip linear replay, YouTube, etc.

            views   = int(item.get("views") or item.get("viewers") or 0)
            minutes = int(item.get("minutesWatched") or item.get("minutes") or 0)
            genre   = (item.get("genre") or item.get("subGenre") or "").strip()

            records.append(VodRecord(
                series_id=_vod_series_id(title, platform),
                title=title,
                platform=platform,
                date_of_activity=activity_date,
                views=views,
                minutes_watched=minutes,
                genre=genre,
            ))
        return records

    except Exception as exc:
        print(f"    [vod] {activity_date}: {exc}")
        return []


def ingest_vod_incremental(
    existing: dict[str, list[VodRecord]],
    days: int = 14,
    today: date | None = None,
) -> dict[str, list[VodRecord]]:
    """Incrementally update VOD universe. Fetches only missing days.

    VOD data is available with a 1-day lag (yesterday at earliest).
    On first run (empty existing) fetches the full trailing `days`.
    """
    today    = today or date.today()
    end_date = today - timedelta(days=1)      # VOD available from yesterday
    cutoff   = today - timedelta(days=days)

    all_dates = {r.date_of_activity for recs in existing.values() for r in recs}

    if not all_dates:
        fetch_start = cutoff
        print(f"  [vod] Full fetch: {fetch_start} → {end_date}")
    else:
        last_cached = max(all_dates)
        fetch_start = last_cached + timedelta(days=1)
        if fetch_start > end_date:
            print(f"  [vod] Cache up to date (latest: {last_cached})")
            return _prune_vod(existing, cutoff)
        days_to_fetch = (end_date - last_cached).days
        print(f"  [vod] Incremental: {fetch_start} → {end_date} ({days_to_fetch} day(s))")

    current = fetch_start
    while current <= end_date:
        records = fetch_vod_day(current)
        for rec in records:
            existing.setdefault(rec.series_id, []).append(rec)
        if records:
            print(f"    [vod] {current}: {len(records)} streaming titles")
        current += timedelta(days=1)
        time.sleep(0.1)

    return _prune_vod(existing, cutoff)


def _prune_vod(
    universe: dict[str, list[VodRecord]],
    cutoff: date,
) -> dict[str, list[VodRecord]]:
    for sid in list(universe.keys()):
        universe[sid] = sorted(
            (r for r in universe[sid] if r.date_of_activity >= cutoff),
            key=lambda r: r.date_of_activity,
        )
        if not universe[sid]:
            del universe[sid]

    total = sum(len(v) for v in universe.values())
    print(f"  [vod] Universe: {len(universe)} streaming titles, {total} snapshots")
    return universe
