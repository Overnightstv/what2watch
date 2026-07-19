"""VOD-specific metrics computed from streaming snapshots.

Each VodRecord is one day's 7-day-rolling figure from BARB.
We derive:
  trend       - week-over-week view growth (0-100)
  consistency - days appearing in charts (loyalty proxy)
  binge_score - avg session length relative to 60-min benchmark (0-100)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from statistics import median

from overnight.models import VodRecord


@dataclass
class VodMetrics:
    """Derived, safe-to-share metrics for one streaming title."""
    series_id: str
    title: str
    platform: str
    genre: str
    computed_at: date
    trend: float          # 0-100: week-over-week view growth
    consistency: int      # days in charts (capped at window size)
    binge_score: float    # 0-100: avg session length vs 60-min benchmark
    latest_views: int
    peak_views: int


def compute_vod_metrics(
    records: list[VodRecord],
    computed_at: date,
) -> VodMetrics:
    if not records:
        raise ValueError("empty records")

    records = sorted(records, key=lambda r: r.date_of_activity)
    latest  = records[-1]
    oldest  = records[0]

    # Trend: view growth from oldest to latest snapshot
    if oldest.views > 0 and latest.date_of_activity != oldest.date_of_activity:
        pct_change = (latest.views - oldest.views) / oldest.views * 100
        # Normalise: 0% growth = 50, +100% = 75, -100% = 25, clamp 0-100
        trend = min(100.0, max(0.0, 50.0 + pct_change / 4))
    elif oldest.views == 0:
        trend = 75.0  # brand new debut — assume momentum
    else:
        trend = 50.0  # single day, can't tell

    # Consistency: number of daily snapshots (more = sustained viewership)
    consistency = len(records)

    # Binge score: average minutes per viewer across all snapshots
    sessions = [
        r.minutes_watched / r.views
        for r in records if r.views > 0 and r.minutes_watched > 0
    ]
    avg_session = sum(sessions) / len(sessions) if sessions else 0.0
    binge_score = min(100.0, avg_session / 60.0 * 100.0)  # 60 min/viewer = 100

    return VodMetrics(
        series_id    = latest.series_id,
        title        = latest.title,
        platform     = latest.platform,
        genre        = latest.genre,
        computed_at  = computed_at,
        trend        = trend,
        consistency  = consistency,
        binge_score  = binge_score,
        latest_views = latest.views,
        peak_views   = max(r.views for r in records),
    )


def compute_all_vod_metrics(
    vod_universe: dict[str, list[VodRecord]],
    computed_at: date,
) -> dict[str, VodMetrics]:
    out: dict[str, VodMetrics] = {}
    for sid, records in vod_universe.items():
        try:
            out[sid] = compute_vod_metrics(records, computed_at)
        except Exception:
            pass
    return out


def vod_median_views(vod_metrics: dict[str, VodMetrics]) -> int:
    """Median latest_views across all titles — used as relative reach floor."""
    views = [vm.latest_views for vm in vod_metrics.values() if vm.latest_views > 0]
    return int(median(views)) if views else 500_000
