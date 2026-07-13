"""BARB ingest — produces EpisodeRecord objects from the Overnights.tv API.

Pulls trailing N days of primetime data across the 5 main channels,
fetches Live+VOSDAL figures for each episode, and attaches 8-week slot
averages. Raw figures live only here and in EpisodeRecord — they never
cross into the copy or delivery layers (models.py compliance firewall).
"""
from __future__ import annotations

import os
import re
import time
from datetime import date, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

from overnight.models import EpisodeRecord

load_dotenv(Path(__file__).parents[3] / ".env")

BASE_URL = os.getenv("OVERNIGHTS_BASE_URL", "https://api.on-tv.tech/api")

# Total channel codes (BBC has no Total — use national code)
CHANNELS: dict[str, int] = {
    "BBC1":      10,
    "BBC2":      20,
    "ITV1":      10030,
    "Channel 4": 10042,
    "Channel 5": 10050,
}

AUDIENCE_CATEGORY = 100   # All Individuals 4+
PRIMETIME_START   = 18 * 60   # 18:00 in minutes
PRIMETIME_END     = 23 * 60   # 23:00 in minutes

SKIP_RE = re.compile(
    r"^(News|Weather|Newsnight|Question Time|Panorama|Breakfast|"
    r"Good Morning|Loose Women|This Morning|Lorraine|Sign Zone|"
    r"CBeebies|CBBC|Close|Test Card|Regional|Local|Junction|"
    r"Presentation|EastEnders|Coronation Street|Emmerdale|Hollyoaks|"
    r"Match of the Day|Football|Rugby|Cricket|Sport)",
    re.I,
)

# Cache slot averages — keyed by (station_code, dow, start_hhmm, end_hhmm)
_slot_cache: dict[tuple, float | None] = {}


# ── HTTP helpers ───────────────────────────────────────────────────────────────

def _header(token_env: str) -> dict:
    token = os.getenv(token_env, "")
    if not token.startswith("Bearer "):
        token = f"Bearer {token}"
    return {"Authorization": token, "Accept": "application/json"}


def _get(path: str, params: dict, token_env: str = "OVERNIGHTS_API_TOKEN") -> dict:
    resp = requests.get(
        f"{BASE_URL}{path}",
        params=params,
        headers=_header(token_env),
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


# ── Helpers ────────────────────────────────────────────────────────────────────

def _hhmm_to_mins(hhmm: int) -> int:
    return (hhmm // 100) * 60 + (hhmm % 100)


def _hhmm_to_str(hhmm: int) -> str:
    return f"{hhmm // 100:02d}:{hhmm % 100:02d}"


def _series_id(title: str, channel: str) -> str:
    """Stable slug: normalised title + channel key."""
    slug = re.sub(r"\s+", "-", re.sub(r"[^\w\s]", "", title.lower())).strip("-")
    return f"{slug}-{channel.lower().replace(' ', '').replace('4', 'four').replace('5', 'five')}"


def _api_dow(tx_date: date) -> int:
    """Python Mon=0 → API Sun=1, Mon=2, ..., Sat=7."""
    return tx_date.weekday() + 2 if tx_date.weekday() < 6 else 1


# ── API calls ──────────────────────────────────────────────────────────────────

def fetch_top_ratings(tx_date: date, station_code: int) -> list[dict]:
    """Primetime shows for one date/channel via top-ratings (LIVE activity)."""
    try:
        data = _get("/top-ratings", {
            "startDate":             tx_date.isoformat(),
            "endDate":               tx_date.isoformat(),
            "stationCodes":          station_code,
            "activityType":          "LIVE",
            "audienceCategoryNumber": AUDIENCE_CATEGORY,
            "sortBy":                "AUDIENCE",
        })
        return data.get("data", [])
    except Exception as exc:
        print(f"    [ingest] top-ratings {tx_date} stn={station_code}: {exc}")
        return []


def fetch_live_vosdal(
    station_code: int,
    start_hhmm: int,
    end_hhmm: int,
    tx_date: date,
) -> tuple[float, float] | tuple[None, None]:
    """Live+VOSDAL audience (000s) and share (%) for one episode."""
    try:
        data = _get(
            "/programme-report/performance",
            {
                "stationCode":            station_code,
                "startTime":              start_hhmm,
                "endTime":                end_hhmm,
                "dateOfTransmission":     tx_date.isoformat(),
                "activityType":           "LIVE_VOSDAL",
                "audienceCategoryNumber": AUDIENCE_CATEGORY,
                "interval":               5,
            },
            token_env="OVERNIGHTS_PROGRAMME_TOKEN",
        )
        summary = data.get("data", {}).get("summary", {})
        aud   = summary.get("audience")
        share = summary.get("share")
        if aud is not None and share is not None:
            return float(aud), float(share)
    except Exception as exc:
        print(f"    [ingest] performance stn={station_code} {tx_date}: {exc}")
    return None, None


def fetch_slot_average(
    station_code: int,
    dow: int,
    start_hhmm: int,
    end_hhmm: int,
    anchor_date: date,
) -> float | None:
    """8-week trailing average share for a channel/day-of-week/slot."""
    key = (station_code, dow, start_hhmm, end_hhmm)
    if key in _slot_cache:
        return _slot_cache[key]

    end_d   = anchor_date - timedelta(days=1)
    start_d = end_d - timedelta(weeks=8)
    slot    = f"{start_hhmm:04d}-{end_hhmm:04d}"

    try:
        data = _get("/day-parts", {
            "startDate":             start_d.isoformat(),
            "endDate":               end_d.isoformat(),
            "activityType":          "LIVE_VOSDAL",
            "audienceCategoryNumber": AUDIENCE_CATEGORY,
            "stationCode":           station_code,
            "dayParts":              slot,
            "weekdays":              dow,
        })
        entries = data if isinstance(data, list) else data.get("data", [])
        shares  = [e.get("share") for e in entries if e.get("share") is not None]
        result  = sum(shares) / len(shares) if shares else None
    except Exception:
        result = None

    _slot_cache[key] = result
    return result


# ── Main ingest ────────────────────────────────────────────────────────────────

def ingest_trailing_window(
    days: int = 28,
    today: date | None = None,
) -> dict[str, list[EpisodeRecord]]:
    """
    Pull trailing `days` of primetime data across all 5 main channels.
    Returns: series_id → list[EpisodeRecord], sorted oldest-first.

    Note: top-ratings has an ~8-day lag, so the most recent week is skipped.
    """
    today    = today or date.today()
    end_date = today - timedelta(days=8)    # lag buffer
    start_dt = today - timedelta(days=days)

    universe: dict[str, list[EpisodeRecord]] = {}
    seen: set[tuple] = set()

    for channel, station_code in CHANNELS.items():
        print(f"  [ingest] {channel}  {start_dt} → {end_date}")
        current = start_dt

        while current <= end_date:
            shows = fetch_top_ratings(current, station_code)

            for show in shows:
                title      = (show.get("txLogProgrammeName") or "").strip()
                start_hhmm = show.get("programmeStartTime")
                end_hhmm   = show.get("programmeEndTime")

                if not title or start_hhmm is None or end_hhmm is None:
                    continue
                if SKIP_RE.match(title):
                    continue
                if not (PRIMETIME_START <= _hhmm_to_mins(start_hhmm) < PRIMETIME_END):
                    continue

                key = (title.lower(), channel, current)
                if key in seen:
                    continue
                seen.add(key)

                aud, share = fetch_live_vosdal(station_code, start_hhmm, end_hhmm, current)
                if aud is None:
                    time.sleep(0.3)
                    continue

                slot_avg = fetch_slot_average(
                    station_code, _api_dow(current), start_hhmm, end_hhmm, today
                ) or 0.0

                sid = _series_id(title, channel)
                rec = EpisodeRecord(
                    programme_id       = f"{sid}|{current.isoformat()}",
                    series_id          = sid,
                    title              = title,
                    channel            = channel,
                    tx_date            = current,
                    slot_start         = _hhmm_to_str(start_hhmm),
                    audience           = aud,
                    share              = share,
                    slot_avg_share_8wk = slot_avg,
                )
                universe.setdefault(sid, []).append(rec)
                time.sleep(0.15)

            current += timedelta(days=1)

    for recs in universe.values():
        recs.sort(key=lambda r: r.tx_date)

    total = sum(len(v) for v in universe.values())
    print(f"  [ingest] Complete — {len(universe)} series, {total} episodes")
    return universe
