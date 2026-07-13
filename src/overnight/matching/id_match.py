"""PA <-> BARB programme identity resolution. Spec 3.1.

Order: manual mapping table -> exact normalised match -> fuzzy fallback.
Unmatched items return series_id=None and are EXCLUDED from selection
(never guessed), logged for the ops queue.
"""
from __future__ import annotations

import difflib
import re
import unicodedata
from datetime import datetime, timedelta


def normalise_title(title: str) -> str:
    t = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    t = t.lower()
    t = re.sub(r"^(the|a)\s+", "", t)
    t = re.sub(r"\b(new|series \d+|s\d+|ep\.? ?\d+|episode \d+)\b", "", t)
    t = re.sub(r"[^a-z0-9 ]", "", t)
    return re.sub(r"\s+", " ", t).strip()


def normalise_channel(channel: str) -> str:
    aliases = {
        "itv": "itv1", "itv 1": "itv1", "bbc 1": "bbc one", "bbc1": "bbc one",
        "bbc 2": "bbc two", "bbc2": "bbc two", "channel four": "channel 4",
        "c4": "channel 4", "c5": "channel 5", "five": "channel 5",
    }
    c = channel.strip().lower()
    return aliases.get(c, c)


class IdMatcher:
    def __init__(
        self,
        barb_index: list[dict],
        manual_map: dict[str, str] | None = None,
        fuzzy_cutoff: float = 0.88,
        window_minutes: int = 15,
    ):
        """barb_index: [{series_id, title, channel, tx: datetime}, ...]
        manual_map: pa_id -> series_id for known offenders.
        """
        self.manual = manual_map or {}
        self.cutoff = fuzzy_cutoff
        self.window = timedelta(minutes=window_minutes)
        self._exact: dict[tuple[str, str], str] = {}
        self._by_channel: dict[str, list[dict]] = {}
        for rec in barb_index:
            key = (normalise_title(rec["title"]), normalise_channel(rec["channel"]))
            self._exact[key] = rec["series_id"]
            self._by_channel.setdefault(normalise_channel(rec["channel"]), []).append(rec)

    def match(self, pa_id: str, title: str, channel: str, tx: datetime) -> tuple[str | None, str]:
        """Returns (series_id | None, method)."""
        if pa_id in self.manual:
            return self.manual[pa_id], "manual"

        nt, nc = normalise_title(title), normalise_channel(channel)
        if (nt, nc) in self._exact:
            return self._exact[(nt, nc)], "exact"

        candidates = self._by_channel.get(nc, [])
        titles = [normalise_title(c["title"]) for c in candidates]
        close = difflib.get_close_matches(nt, titles, n=1, cutoff=self.cutoff)
        if close:
            for c in candidates:
                if normalise_title(c["title"]) == close[0]:
                    barb_tx = c.get("tx")
                    if barb_tx is None or abs(barb_tx - tx) <= self.window:
                        return c["series_id"], "fuzzy"
        return None, "unmatched"
