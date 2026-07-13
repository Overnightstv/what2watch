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

# Station codes — Total where available (better data), standard otherwise.
# BBC has no Total feed so uses national codes (10, 20).
CHANNELS: dict[str, int] = {
    # Main five
    "BBC One":       10,
    "BBC Two":       20,
    "ITV":           10030,
    "Channel 4":     10042,
    "Channel 5":     10050,
    # BBC
    "BBC Three":     4515,
    "BBC Four":      4632,
    # ITV family
    "ITV2":          14979,
    "ITV3":          14328,
    "ITV4":          14392,
    "ITVBe":         15148,
    # Channel 4 family
    "E4":            14874,
    "More4":         14382,
    "Film4":         14977,
    # Channel 5 family
    "5STAR":         14265,
    "5USA":          14266,
    # Sky
    "Sky Atlantic":      15016,
    "Sky Max":           5315,
    "Sky One":           4924,
    "Sky Showcase":      15313,
    "Sky Comedy":        5289,
    "Sky Witness":       14939,
    "Sky History":       14961,
    "Sky Arts":          14702,
    "Sky Documentaries": 5296,
    "Sky Nature":        5295,
    "Sky Crime":         14340,
    # UKTV / Discovery
    "Dave":          14829,
    "Gold":          14934,
    "W":             14041,
    "Yesterday":     14684,
    "Drama":         15081,
    "Alibi":         14842,
    "Eden":          14971,
    # Other mainstream entertainment
    "Comedy Central":    14957,
    "Quest":             14073,
    "Really":            4349,
    "TLC":               15077,
    "National Geographic": 14964,
    "Crime + Investigation": 14252,
    "Talking Pictures":  5168,
    "Discovery":         14935,
    # Children's
    "CBeebies":          4630,
    "CBBC":              4631,
    "Nickelodeon":       14937,
    "Nick Jr":           14983,
    "Cartoonito":        4236,
    "Boomerang":         14845,
    # Sports
    "Sky Sports Main Event":      4929,
    "Sky Sports Premier League":  5144,
    "Sky Sports Football":        5238,
    "Sky Sports Cricket":         4942,
    "Sky Sports Action":          4945,
    "Sky Sports Golf":            4811,
    "Sky Sports F1":              5032,
    "Sky Sports Racing":          4641,
    "TNT Sports 1":               5086,
    "TNT Sports 2":               5087,
    "TNT Sports 3":               5166,
    "TNT Sports 4":               4090,
    "Eurosport":                  4925,
    "Eurosport 2":                4794,
}

AUDIENCE_CATEGORY = 100   # All Individuals 4+
# top-ratings API returns programmeStartTime/EndTime in seconds from midnight
PRIMETIME_START = 18 * 3600   # 18:00
PRIMETIME_END   = 23 * 3600   # 23:00

SKIP_RE = re.compile(
    r"^(News( at|\s*$)|BBC News|BBC NEWS|Weather|Newsnight|Question Time|Breakfast|"
    r"Good Morning|Loose Women|This Morning|Lorraine|Sign Zone|"
    r"Close|Test Card|Regional|Local|Junction|Presentation)",
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

def _secs_to_hhmm(secs: int) -> int:
    """Seconds-from-midnight → HHMM int (for dayparts API slot format)."""
    h = secs // 3600
    m = (secs % 3600) // 60
    return h * 100 + m


def _secs_to_str(secs: int) -> str:
    """Seconds-from-midnight → HH:MM display string."""
    h = secs // 3600
    m = (secs % 3600) // 60
    return f"{h:02d}:{m:02d}"


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
    start_secs: int,
    end_secs: int,
    tx_date: date,
) -> tuple[float, float] | tuple[None, None]:
    """Live+VOSDAL audience (000s) and share (%) for one episode.

    startTime/endTime are seconds from midnight, as returned by top-ratings.
    """
    try:
        data = _get(
            "/programme-report/performance",
            {
                "stationCode":            station_code,
                "startTime":              start_secs,
                "endTime":                end_secs,
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
    start_secs: int,
    end_secs: int,
    anchor_date: date,
) -> float | None:
    """8-week trailing average share for a channel/day-of-week/slot."""
    key = (station_code, dow, start_secs, end_secs)
    if key in _slot_cache:
        return _slot_cache[key]

    end_d   = anchor_date - timedelta(days=1)
    start_d = end_d - timedelta(weeks=8)
    # dayparts API expects HHMM-HHMM format
    start_hhmm = _secs_to_hhmm(start_secs)
    end_hhmm   = _secs_to_hhmm(end_secs)
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
    end_date = today - timedelta(days=10)   # top-ratings lag ~9-10 days
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
                start_secs = show.get("programmeStartTime")
                end_secs   = show.get("programmeEndTime")

                if not title or start_secs is None or end_secs is None:
                    continue
                if SKIP_RE.match(title):
                    continue
                if not (PRIMETIME_START <= start_secs < PRIMETIME_END):
                    continue

                key = (title.lower(), channel, current)
                if key in seen:
                    continue
                seen.add(key)

                aud, share = fetch_live_vosdal(station_code, start_secs, end_secs, current)
                if aud is None:
                    time.sleep(0.3)
                    continue

                slot_avg = fetch_slot_average(
                    station_code, _api_dow(current), start_secs, end_secs, today
                ) or 0.0

                sid = _series_id(title, channel)
                rec = EpisodeRecord(
                    programme_id       = f"{sid}|{current.isoformat()}",
                    series_id          = sid,
                    title              = title,
                    channel            = channel,
                    tx_date            = current,
                    slot_start         = _secs_to_str(start_secs),
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
