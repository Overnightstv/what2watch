"""Respondent-level affinity. Spec 4.4-4.5.

The minimum cell floor is a compliance and statistical-validity control.
It is deliberately NOT read from config overrides at call sites - it is
loaded once and applied unconditionally. Spec: "never overridden by editorial".
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from overnight.models import ClusterAffinity


@dataclass(frozen=True)
class PanelViewing:
    """One panel home's qualified viewing of one series (>= watched_minutes)."""
    home_id: str
    series_id: str


def pair_lift(
    viewings: list[PanelViewing],
    series_x: str,
    series_y: str,
    min_cell_x: int,
    min_cell_xy: int,
    panel_homes: set[str] | None = None,
) -> float | None:
    """lift(X->Y) = P(Y|X) / P(Y). Returns None below the cell floor.

    panel_homes: the full panel roster. If None, falls back to homes seen
    in viewings - fine for tests, but production must pass the roster so
    non-viewing homes count in the base rate.
    """
    homes = panel_homes if panel_homes is not None else {v.home_id for v in viewings}
    if not homes:
        return None
    watched = defaultdict(set)
    for v in viewings:
        watched[v.series_id].add(v.home_id)

    x_homes = watched.get(series_x, set())
    y_homes = watched.get(series_y, set())
    xy_homes = x_homes & y_homes

    if len(x_homes) < min_cell_x or len(xy_homes) < min_cell_xy:
        return None  # below floor: caller falls back to cluster level

    p_y_given_x = len(xy_homes) / len(x_homes)
    p_y = len(y_homes) / len(homes)
    if p_y == 0:
        return None
    return p_y_given_x / p_y


def cluster_lift(
    viewings: list[PanelViewing],
    home_clusters: dict[str, str],
    series_id: str,
    cluster_id: str,
    min_cell: int,
) -> ClusterAffinity | None:
    """Lift of a series within a taste cluster vs the whole panel.

    lift = P(watched | in cluster) / P(watched | panel).
    Subscribers only ever meet panel data through this aggregate - there is
    no identifier bridge between subscribers and panel homes (spec 6).
    """
    all_homes = set(home_clusters)
    cluster_homes = {h for h, c in home_clusters.items() if c == cluster_id}
    if len(cluster_homes) < min_cell:
        return None

    watchers = {v.home_id for v in viewings if v.series_id == series_id}
    cluster_watchers = watchers & cluster_homes
    if len(cluster_watchers) < min_cell:
        return None

    p_cluster = len(cluster_watchers) / len(cluster_homes)
    p_panel = len(watchers & all_homes) / len(all_homes) if all_homes else 0
    if p_panel == 0:
        return None
    return ClusterAffinity(
        series_id=series_id,
        cluster_id=cluster_id,
        lift=p_cluster / p_panel,
        cell_size=len(cluster_watchers),
    )
