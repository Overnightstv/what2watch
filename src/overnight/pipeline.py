"""Nightly orchestration skeleton. Spec section 2.

The TODO markers are the wiring points for Claude Code against the
live environment: BARB store, PA feed, subscriber DB, ESP/WhatsApp.
Everything above them is already implemented and tested.
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import yaml

from overnight.matching.id_match import IdMatcher
from overnight.metrics.scores import compute_series_metrics
from overnight.selection.engine import SelectionEngine
from overnight.copygen.generate import generate_copy

CFG = yaml.safe_load((Path(__file__).parents[2] / "config" / "thresholds.yaml").read_text())


def run_nightly(now: datetime | None = None) -> None:
    now = now or datetime.now()

    # 1. Ingest overnights -> EpisodeRecords
    # TODO(wiring): pull from the existing Overnights.tv BARB store
    universe = {}  # series_id -> list[EpisodeRecord], trailing 28 days

    # 2. Derived metrics per series
    metrics = {
        sid: compute_series_metrics(eps, universe, CFG, computed_at=now.date())
        for sid, eps in universe.items()
    }

    # 3. PA forward schedule + ID matching
    # TODO(wiring): fetch PA schedule (tonight + 14d) and imagery refs
    matcher = IdMatcher(barb_index=[])  # TODO(wiring): build from BARB store
    schedule = []  # list[ScheduleItem] with series_id resolved via matcher

    # 4. Selection per cluster
    engine = SelectionEngine(CFG, history=[])  # TODO(wiring): load 7-day send history
    clusters = ["default"]  # TODO(wiring): load cluster ids
    for cluster_id in clusters:
        candidates = engine.build_candidates(schedule, metrics, now)
        edition = engine.allocate(candidates, edition_date=now.date(), cluster_id=cluster_id)

        # 5. Copy generation + lint (blocks to review on any issue)
        result = generate_copy(edition)
        if result["status"] == "blocked":
            # TODO(wiring): push to review queue with result["lint"]
            continue
        # TODO(wiring): render templates, queue for human review, send at 17:30


if __name__ == "__main__":
    run_nightly()
