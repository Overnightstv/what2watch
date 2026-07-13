"""Derived metric computation. Spec section 4.

Inputs are raw EpisodeRecords; outputs are 0-100 percentile-normalised
scores safe to cross the compliance firewall.
"""
from __future__ import annotations

import statistics
from datetime import date

from overnight.models import EpisodeRecord, SeriesMetrics


def percentile_rank(value: float, population: list[float]) -> float:
    """0-100 rank of value within population. Empty/degenerate -> 50."""
    if not population:
        return 50.0
    below = sum(1 for v in population if v < value)
    equal = sum(1 for v in population if v == value)
    return 100.0 * (below + 0.5 * equal) / len(population)


def _episodes_sorted(episodes: list[EpisodeRecord]) -> list[EpisodeRecord]:
    return sorted(episodes, key=lambda e: e.tx_date)


def wow_delta(episodes: list[EpisodeRecord]) -> float:
    """Latest week-on-week audience change, as a fraction (0.1 = +10%)."""
    eps = _episodes_sorted(episodes)
    if len(eps) < 2 or eps[-2].audience == 0:
        return 0.0
    return (eps[-1].audience - eps[-2].audience) / eps[-2].audience


def slot_relative(episodes: list[EpisodeRecord]) -> float:
    """Latest episode share vs trailing slot average. 1.3 = 30% above slot norm."""
    eps = _episodes_sorted(episodes)
    latest = eps[-1]
    if latest.slot_avg_share_8wk <= 0:
        return 1.0
    return latest.share / latest.slot_avg_share_8wk


def growth_streak(episodes: list[EpisodeRecord]) -> int:
    """Consecutive most-recent weeks of positive audience growth."""
    eps = _episodes_sorted(episodes)
    streak = 0
    for prev, cur in zip(reversed(eps[:-1]), reversed(eps[1:])):
        if cur.audience > prev.audience:
            streak += 1
        else:
            break
    return streak


def decline_streak(episodes: list[EpisodeRecord]) -> int:
    eps = _episodes_sorted(episodes)
    streak = 0
    for prev, cur in zip(reversed(eps[:-1]), reversed(eps[1:])):
        if cur.audience < prev.audience:
            streak += 1
        else:
            break
    return streak


def retention(episodes: list[EpisodeRecord]) -> float:
    """Mean episode-on-episode retention ratio (ep_n / ep_n-1)."""
    eps = _episodes_sorted(episodes)
    ratios = [
        b.audience / a.audience
        for a, b in zip(eps, eps[1:])
        if a.audience > 0
    ]
    return statistics.mean(ratios) if ratios else 1.0


def consistency(episodes: list[EpisodeRecord]) -> float:
    """Inverse coefficient of variation of episode audiences (higher = steadier)."""
    auds = [e.audience for e in episodes]
    if len(auds) < 2 or statistics.mean(auds) == 0:
        return 1.0
    cv = statistics.stdev(auds) / statistics.mean(auds)
    return 1.0 / (1.0 + cv)


def catchup_growth(episodes: list[EpisodeRecord]) -> float:
    """Mean VOD/consolidated uplift fraction where the feed supports it."""
    ups = [e.vod_uplift_pct for e in episodes if e.vod_uplift_pct is not None]
    return statistics.mean(ups) if ups else 0.0


def compute_series_metrics(
    series_episodes: list[EpisodeRecord],
    universe: dict[str, list[EpisodeRecord]],
    cfg: dict,
    computed_at: date,
    series_complete: bool = False,
    completed_on: date | None = None,
) -> SeriesMetrics:
    """Compute normalised Momentum, Loyalty, Reach for one series against
    the universe of all series measured in the normalisation window.

    `universe` maps series_id -> episodes for every series in the trailing
    window (including this one).
    """
    mcfg = cfg["metrics"]["momentum"]
    lcfg = cfg["metrics"]["loyalty"]

    def raw_momentum(eps: list[EpisodeRecord]) -> float:
        streak_bonus = min(growth_streak(eps), 5) / 5.0
        return (
            mcfg["w_wow_delta"] * wow_delta(eps)
            + mcfg["w_slot_relative"] * slot_relative(eps)
            + mcfg["w_streak"] * streak_bonus
        )

    def raw_loyalty(eps: list[EpisodeRecord]) -> float:
        return (
            lcfg["w_retention"] * retention(eps)
            + lcfg["w_consistency"] * consistency(eps)
            + lcfg["w_catchup"] * catchup_growth(eps)
        )

    def avg_audience(eps: list[EpisodeRecord]) -> float:
        return statistics.mean(e.audience for e in eps) if eps else 0.0

    momentum_pop = [raw_momentum(eps) for eps in universe.values() if len(eps) >= 1]
    loyalty_pop = [raw_loyalty(eps) for eps in universe.values() if len(eps) >= 2]
    reach_pop = [avg_audience(eps) for eps in universe.values()]

    latest = _episodes_sorted(series_episodes)[-1]
    return SeriesMetrics(
        series_id=latest.series_id,
        title=latest.title,
        channel=latest.channel,
        computed_at=computed_at,
        momentum=percentile_rank(raw_momentum(series_episodes), momentum_pop),
        loyalty=percentile_rank(raw_loyalty(series_episodes), loyalty_pop)
        if len(series_episodes) >= 2 else 50.0,
        reach=percentile_rank(avg_audience(series_episodes), reach_pop),
        streak_weeks=growth_streak(series_episodes),
        episodes_measured=len(series_episodes),
        series_complete=series_complete,
        completed_on=completed_on,
    )


def band(score: float) -> str:
    """Qualitative band for consumer-safe evidence (spec 6: no precise ratios)."""
    if score >= 90:
        return "top decile"
    if score >= 70:
        return "high"
    if score >= 40:
        return "mid"
    return "low"
