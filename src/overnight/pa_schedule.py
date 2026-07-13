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

# PA channel UUIDs — covers all channels in the BARB ingest
PA_CHANNEL_IDS: dict[str, str] = {
    # Main five
    "BBC One":       "78bd54a7-6883-575e-a84f-f328dc424979",
    "BBC Two":       "7a6cd877-f7c0-5d40-8862-0ad24318b712",
    "ITV":           "f0eac74d-2245-57c9-9dfa-d8a54da0c066",
    "Channel 4":     "fd66055a-5bc7-51e8-8421-df88e60c98b3",
    "Channel 5":     "bbc2a081-fc85-5c05-8ff0-4438cc97de2c",
    # BBC
    "BBC Three":     "fea6e9fd-c98c-5209-849f-a486ac600ff7",
    "BBC Four":      "f2fb2e91-2578-5975-a7cc-dd048186ea52",
    # ITV family
    "ITV2":          "64b0d836-b2b1-5a9c-a077-99fbd5dff799",
    "ITV3":          "8dc66a04-043a-5c8e-aab3-a32343bd2016",
    "ITV4":          "76c14546-b2bd-51d4-9760-4b24820686e2",
    # Channel 4 family
    "E4":            "b5442f3a-c482-50d1-96d5-f25fd16f3e37",
    "More4":         "d4e9648c-12e6-586a-9265-f2878a610884",
    "Film4":         "51b4a470-009c-5a13-bb8d-1f1090796c3c",
    # Channel 5 family
    "5STAR":         "9512892c-d864-513c-b630-abb5df6b49f4",
    "5USA":          "4b75d2ae-672b-5d6f-b1d8-a0b2761fde3a",
    # Sky — PA still uses "Sky One" for what BARB calls Sky Max (same channel, rebranded 2022)
    "Sky Atlantic":      "5c56dba8-10dd-5b7e-aa9d-0317867ec2c9",
    "Sky Max":           "f950225d-037e-56ad-bd49-7d210648a4c9",  # PA name: "Sky One"
    "Sky One":           "f950225d-037e-56ad-bd49-7d210648a4c9",
    "Sky Showcase":      "37f6c091-46bc-51f2-b7ca-d8f2a2a361e8",  # PA has channel but sparse schedule
    "Sky Comedy":        "772bbf4c-ecaf-5ddf-9cb4-4fda07120da9",
    "Sky Witness":       "6e9dc822-69e5-5568-996a-ccce413791c2",
    "Sky History":       "9a4b80a8-7844-5be0-822f-98457b693eaa",
    "Sky Arts":          "eb5f203b-bdc5-5d30-a700-5cabb4d6a085",
    "Sky Documentaries": "d905c697-22eb-5d35-8e98-84bd08c3ad90",
    "Sky Nature":        "c7cb366a-a0d0-5f8e-a1e3-828ae6a2a336",
    "Sky Crime":         "af9e4bd4-1414-5ef4-b77a-50461be96967",
    # UKTV
    "Dave":          "715f4b2b-b1a9-5ce4-aaa0-7de29db1bbfe",
    "Gold":          "83a00f21-a841-5d34-8566-522c17c13253",
    "W":             "51cb8909-a482-5fdf-9570-9e147e485568",
    "Yesterday":     "d1c48f93-3ae1-5c35-9338-31d5f7227c7b",
    "Drama":         "30050daf-5792-5ca4-90f9-f2f776be9d1f",
    "Alibi":         "e0d7df16-df4b-5bc6-bd59-25711dc11018",
    "Eden":          "e111fb1a-e3f4-5e78-bc1b-54343dbe2f5f",
    # Other mainstream
    "Comedy Central":    "b13e2396-4327-569c-8e7a-18cead510f39",
    "Quest":             "c0ad2509-49f0-508b-aaa3-9693382e1d31",
    "Really":            "fd22499a-bc88-5836-a639-7fad80310d7f",
    "TLC":               "a48438f4-a999-5da5-8799-6761e3728cec",
    "National Geographic": "af107972-5014-53b4-9cba-85802323e740",
    "Talking Pictures":  "98a1baa0-0534-5a59-af55-f7065f52bbd0",
    "Discovery":         "3d70e024-4435-5cec-b070-84ff71a8581e",
    # Children's
    "CBeebies":          "27b85aec-51fc-5c55-a483-24ff7c70186b",
    "CBBC":              "1f6ecdb4-985e-5ab3-adfc-022a5f63c6d1",
    "Nickelodeon":       "3eabb761-96d7-5129-9d79-63f3128fe335",
    "Nick Jr":           "7236be36-ddcb-5d5f-84b8-8e801f2b88fa",
    "Cartoonito":        "f5342f90-2dff-5ad7-9d9a-5179111dda82",
    "Boomerang":         "9b7f3740-1e34-5049-aa64-4726fccf7cc4",
    # Sports
    "Sky Sports Main Event":      "4a456557-c479-5190-bedc-ae061c5771c2",
    "Sky Sports Premier League":  "7275c5c5-d73a-55ce-80b4-b9848d9d6e65",
    "Sky Sports Football":        "42899af8-f668-579a-ae1f-eaf567de0665",
    "Sky Sports Cricket":         "054dd24d-cedd-5a73-bfd6-0df4d6a25c41",
    "Sky Sports Action":          "4e7565b2-292c-5b52-b76a-d27e0e573f3f",
    "Sky Sports Golf":            "a679a3b0-ea3a-5359-9b29-f37f62280e41",
    "Sky Sports F1":              "eb58ffa6-d395-56e1-a56b-df611c2ff32a",
    "Sky Sports Racing":          "34d6515d-28f2-5640-834b-e5149f782736",
    "TNT Sports 1":               "0e28fe4a-9473-5ea0-955d-eba6f0e8c705",
    "TNT Sports 2":               "d90b2d7c-af93-5d8d-a176-66daf48c7a09",
    "TNT Sports 3":               "2a4a9d4f-2cf7-5acf-81a8-4f3b474b844d",
    "TNT Sports 4":               "ef96324e-87a9-5de8-bbbd-411d927549ca",
    # ITVBe — not in PA feed, BARB-only
    # Sky Showcase — PA channel registered but no schedule data yet, BARB-only
    # Eurosport — not in PA feed, BARB-only
}

# Default catchup platforms per channel
CATCHUP: dict[str, str] = {
    "BBC One":       "BBC iPlayer",
    "BBC Two":       "BBC iPlayer",
    "BBC Three":     "BBC iPlayer",
    "BBC Four":      "BBC iPlayer",
    "ITV":           "ITVX",
    "ITV2":          "ITVX",
    "ITV3":          "ITVX",
    "ITV4":          "ITVX",
    "ITVBe":         "ITVX",
    "Channel 4":     "Channel 4",
    "E4":            "Channel 4",
    "More4":         "Channel 4",
    "Film4":         "Channel 4",
    "Channel 5":     "My5",
    "5STAR":         "My5",
    "5USA":          "My5",
    "Sky Atlantic":      "Sky Go / Now",
    "Sky Max":           "Sky Go / Now",
    "Sky One":           "Sky Go / Now",
    "Sky Showcase":      "Sky Go / Now",
    "Sky Comedy":        "Sky Go / Now",
    "Sky Witness":       "Sky Go / Now",
    "Sky History":       "Sky Go / Now",
    "Sky Arts":          "Sky Go / Now",
    "Sky Documentaries": "Sky Go / Now",
    "Sky Nature":        "Sky Go / Now",
    "Sky Crime":         "Sky Go / Now",
    "Dave":          "UKTV Play",
    "Gold":          "UKTV Play",
    "W":             "UKTV Play",
    "Yesterday":     "UKTV Play",
    "Drama":         "UKTV Play",
    "Alibi":         "UKTV Play",
    "Eden":          "UKTV Play",
    "Comedy Central":    "Paramount+",
    "Quest":             "Discovery+",
    "Really":            "Discovery+",
    "TLC":               "Discovery+",
    "National Geographic": "Disney+",
    "Talking Pictures":  "Talking Pictures TV",
    "Discovery":         "Discovery+",
    # Children's
    "CBeebies":          "BBC iPlayer",
    "CBBC":              "BBC iPlayer",
    "Nickelodeon":       "Paramount+",
    "Nick Jr":           "Paramount+",
    "Cartoonito":        "Sky Go / Now",
    "Boomerang":         "Boomerang",
    # Sports
    "Sky Sports Main Event":      "Sky Go / Now",
    "Sky Sports Premier League":  "Sky Go / Now",
    "Sky Sports Football":        "Sky Go / Now",
    "Sky Sports Cricket":         "Sky Go / Now",
    "Sky Sports Action":          "Sky Go / Now",
    "Sky Sports Golf":            "Sky Go / Now",
    "Sky Sports F1":              "Sky Go / Now",
    "Sky Sports Racing":          "Sky Go / Now",
    "TNT Sports 1":               "Discovery+",
    "TNT Sports 2":               "Discovery+",
    "TNT Sports 3":               "Discovery+",
    "TNT Sports 4":               "Discovery+",
    "Eurosport":                  "Discovery+",
    "Eurosport 2":                "Discovery+",
}

LIVE_EVENT_RE = re.compile(
    r"\b(live|final|grand final|cup|semi.final|election|awards|ceremony)\b", re.I
)

PRIMETIME_START = 18 * 60
PRIMETIME_END   = 23 * 60

SKIP_RE = re.compile(
    r"^(News( at|\s*$)|Weather|Newsnight|Question Time|Breakfast|"
    r"Good Morning|Loose Women|This Morning|Sign Zone|"
    r"Close|Test Card|Regional|Local|Junction|Presentation)",
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
