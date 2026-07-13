from datetime import date, datetime

import yaml
from pathlib import Path

from overnight.metrics.scores import compute_series_metrics
from overnight.models import AlertType, ScheduleItem
from overnight.selection.engine import SelectionEngine

CFG = yaml.safe_load((Path(__file__).parents[1] / "config" / "thresholds.yaml").read_text())
NOW = datetime(2026, 7, 6, 9, 30)


def _metrics(universe):
    return {sid: compute_series_metrics(e, universe, CFG, NOW.date()) for sid, e in universe.items()}


def _schedule():
    return [
        ScheduleItem(pa_id="p1", series_id="hit", title="Coldwater", channel="ITV1",
                     tx=datetime(2026, 7, 6, 21, 0), genre="drama",
                     availability=["ITV1", "ITVX"], image_ref="pa:1"),
        ScheduleItem(pa_id="p2", series_id="gem", title="Harbour Lights", channel="Channel 5",
                     tx=datetime(2026, 7, 6, 20, 0), genre="factual",
                     availability=["Channel 5", "My5"], image_ref="pa:2"),
        ScheduleItem(pa_id="p3", series_id=None, title="Unmatched Show", channel="ITV1",
                     tx=datetime(2026, 7, 6, 22, 0), genre="drama"),
    ]


def test_banker_and_rising_selected(universe):
    eng = SelectionEngine(CFG)
    cands = eng.build_candidates(_schedule(), _metrics(universe), NOW)
    types = {c.alert_type for c in cands}
    assert AlertType.BANKER in types
    assert AlertType.RISING in types


def test_unmatched_items_excluded(universe):
    eng = SelectionEngine(CFG)
    cands = eng.build_candidates(_schedule(), _metrics(universe), NOW)
    assert all(c.series_id is not None for c in cands)


def test_evidence_contains_no_raw_figures(universe):
    eng = SelectionEngine(CFG)
    cands = eng.build_candidates(_schedule(), _metrics(universe), NOW)
    for c in cands:
        for v in c.evidence.values():
            assert not (isinstance(v, float) and v > 100), "raw figure leaked into evidence"


def test_allocation_dedups_and_caps(universe):
    eng = SelectionEngine(CFG)
    cands = eng.build_candidates(_schedule(), _metrics(universe), NOW)
    ed = eng.allocate(cands, NOW.date(), "default")
    assert len(ed.items) <= CFG["slots"]["max_items_per_day"]
    ids = [i.series_id for i in ed.items]
    assert len(ids) == len(set(ids))


def test_quiet_day_when_nothing_qualifies():
    eng = SelectionEngine(CFG)
    ed = eng.allocate([], date(2026, 7, 8), "default")
    assert ed.quiet_day is True
