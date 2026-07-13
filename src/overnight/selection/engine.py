"""Alert selection. Spec section 5.

Every alert type: eligibility gate -> ranking score -> slot allocation
with dedup and frequency caps. Evidence dicts contain ONLY bands,
streaks and qualitative descriptors (spec 6 firewall).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from overnight.metrics.scores import band
from overnight.models import (
    AlertItem, AlertType, ClusterAffinity, Edition, ScheduleItem, SeriesMetrics,
)


class SelectionEngine:
    def __init__(self, cfg: dict, history: list[AlertItem] | None = None):
        """history: alerts sent in the trailing 7 days (for dedup/caps)."""
        self.cfg = cfg
        self.g = cfg["gates"]
        self.s = cfg["slots"]
        self.history = history or []

    # ---------- gates ----------

    def _mentions_last_7d(self, series_id: str) -> int:
        return sum(1 for a in self.history if a.series_id == series_id)

    def eligible_banker(self, m: SeriesMetrics, item: ScheduleItem, now: datetime) -> bool:
        g = self.g["banker"]
        return (
            item.tx.date() == now.date()
            and m.momentum >= g["min_momentum"]
            and m.reach >= g["min_reach"]
        )

    def eligible_rising(self, m: SeriesMetrics, item: ScheduleItem, now: datetime) -> bool:
        g = self.g["rising"]
        return (
            now <= item.tx <= now + timedelta(hours=g["hours_ahead"])
            and m.streak_weeks >= g["min_streak_weeks"]
            and m.reach <= g["max_reach"]
            and m.loyalty >= g["min_loyalty"]
        )

    def eligible_binge(self, m: SeriesMetrics, today: date) -> bool:
        g = self.g["binge_verdict"]
        return (
            m.series_complete
            and m.completed_on is not None
            and (today - m.completed_on).days <= g["completed_within_days"]
            and m.loyalty >= g["min_loyalty"]
            and m.episodes_measured >= g["min_episodes"]
        )

    def eligible_gem(self, m: SeriesMetrics, aff: ClusterAffinity | None,
                     on_catchup: bool) -> bool:
        g = self.g["weekly_gem"]
        return (
            aff is not None
            and m.loyalty >= g["min_loyalty"]
            and m.reach <= g["max_reach"]
            and aff.lift >= g["min_lift"]
            and on_catchup
        )

    # ---------- candidate building ----------

    def _evidence(self, m: SeriesMetrics) -> dict:
        return {
            "momentum_band": band(m.momentum),
            "loyalty_band": band(m.loyalty),
            "reach_band": band(m.reach),
            "streak_weeks": m.streak_weeks,
        }

    def build_candidates(
        self,
        schedule: list[ScheduleItem],
        metrics: dict[str, SeriesMetrics],
        now: datetime,
    ) -> list[AlertItem]:
        out: list[AlertItem] = []
        for item in schedule:
            if item.series_id is None:
                continue  # unmatched -> excluded, never guessed (spec 3.1)
            m = metrics.get(item.series_id)
            if m is None:
                continue
            common = dict(
                series_id=item.series_id, title=item.title, channel=item.channel,
                tx=item.tx, availability=item.availability, image_ref=item.image_ref,
            )
            if self.eligible_banker(m, item, now):
                out.append(AlertItem(
                    alert_type=AlertType.BANKER, evidence=self._evidence(m),
                    score=m.momentum, **common))
            if self.eligible_rising(m, item, now):
                out.append(AlertItem(
                    alert_type=AlertType.RISING, evidence=self._evidence(m),
                    score=m.loyalty * (1 + m.streak_weeks / 10), **common))
            if item.is_live_event:
                pct = m.momentum  # comparable-event percentile proxy in v1
                if pct >= self.g["live_event"]["min_comparable_percentile"]:
                    out.append(AlertItem(
                        alert_type=AlertType.LIVE_EVENT,
                        evidence={**self._evidence(m), "comparable_band": band(pct)},
                        score=pct, **common))

        today = now.date()
        for m in metrics.values():
            if self.eligible_binge(m, today):
                out.append(AlertItem(
                    alert_type=AlertType.BINGE_VERDICT, series_id=m.series_id,
                    title=m.title, channel=m.channel, tx=None,
                    availability=[], image_ref=None,
                    evidence=self._evidence(m), score=m.loyalty))
        return out

    # ---------- slot allocation ----------

    def allocate(self, candidates: list[AlertItem], edition_date: date,
                 cluster_id: str) -> Edition:
        chosen: list[AlertItem] = []
        used_series: set[str] = set()
        priority = [AlertType(t) for t in self.s["priority"]]

        for atype in priority:
            pool = sorted(
                (c for c in candidates if c.alert_type == atype),
                key=lambda c: c.score, reverse=True,
            )
            for cand in pool:
                if len(chosen) >= self.s["max_items_per_day"]:
                    break
                if cand.series_id in used_series:
                    continue
                if self._mentions_last_7d(cand.series_id) >= self.s["max_mentions_per_7_days"]:
                    if not self._banker_exception(cand):
                        continue
                chosen.append(cand)
                used_series.add(cand.series_id)
                break  # one per type per day

        quiet = len(chosen) < self.s["min_items_per_day"]
        return Edition(edition_date=edition_date, cluster_id=cluster_id,
                       items=chosen, quiet_day=quiet)

    def _banker_exception(self, cand: AlertItem) -> bool:
        """A Banker may run episode-to-episode up to the consecutive cap."""
        if cand.alert_type != AlertType.BANKER:
            return False
        recent = [a for a in self.history
                  if a.series_id == cand.series_id and a.alert_type == AlertType.BANKER]
        return len(recent) < self.s["banker_consecutive_cap"]
