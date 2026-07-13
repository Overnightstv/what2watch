from datetime import date

import yaml
from pathlib import Path

from overnight.metrics.scores import (
    compute_series_metrics, growth_streak, decline_streak, retention, band,
)
from overnight.metrics.affinity import PanelViewing, pair_lift, cluster_lift

CFG = yaml.safe_load((Path(__file__).parents[1] / "config" / "thresholds.yaml").read_text())


def test_growth_and_decline_streaks(universe):
    assert growth_streak(universe["gem"]) == 3
    assert decline_streak(universe["fade"]) == 3
    assert growth_streak(universe["fade"]) == 0


def test_retention_steady_series_near_one(universe):
    assert 0.98 < retention(universe["steady"]) < 1.02


def test_gem_has_high_loyalty_low_reach(universe):
    m = compute_series_metrics(universe["gem"], universe, CFG, date(2026, 7, 6))
    assert m.loyalty >= 70, m.loyalty
    assert m.reach <= 30, m.reach
    assert m.streak_weeks >= 3


def test_hit_has_high_momentum_and_reach(universe):
    m = compute_series_metrics(universe["hit"], universe, CFG, date(2026, 7, 6))
    assert m.momentum >= 70
    assert m.reach >= 70


def test_bands():
    assert band(95) == "top decile"
    assert band(75) == "high"
    assert band(10) == "low"


def _panel(n_homes=200, gem_watchers=60, overlap=45):
    """hit watched by half the panel; gem by gem_watchers; overlap of those watch both."""
    views = []
    for h in range(n_homes // 2):
        views.append(PanelViewing(f"h{h}", "hit"))
    for h in range(gem_watchers):
        views.append(PanelViewing(f"h{h if h < overlap else h + 100}", "gem"))
    return views


def test_pair_lift_above_floor():
    roster = {f"h{i}" for i in range(200)}
    lift = pair_lift(_panel(), "hit", "gem", min_cell_x=50, min_cell_xy=30, panel_homes=roster)
    assert lift is not None and lift > 1.0


def test_pair_lift_blocked_below_floor():
    views = _panel(n_homes=60, gem_watchers=10, overlap=8)
    assert pair_lift(views, "hit", "gem", min_cell_x=50, min_cell_xy=30) is None


def test_cluster_lift_respects_floor():
    views = [PanelViewing(f"h{i}", "gem") for i in range(40)]
    clusters = {f"h{i}": ("drama" if i < 80 else "sport") for i in range(200)}
    assert cluster_lift(views, clusters, "gem", "drama", min_cell=50) is None
    ca = cluster_lift(views, clusters, "gem", "drama", min_cell=30)
    assert ca is not None and ca.lift > 1.0
