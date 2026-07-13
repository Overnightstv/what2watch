"""Core data models. Spec ref: sections 3-5.

Raw BARB values live only in EpisodeRecord and never cross into
AlertItem / CopyPayload - that boundary is the compliance firewall
(spec section 6).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Optional


class AlertType(str, Enum):
    BANKER = "banker"
    RISING = "rising"
    BINGE_VERDICT = "binge_verdict"
    LIVE_EVENT = "live_event"
    WEEKLY_GEM = "weekly_gem"
    SKIP_WARNING = "skip_warning"


@dataclass
class EpisodeRecord:
    """One episode's measured performance. RAW - never leaves the metrics layer."""
    programme_id: str
    series_id: str
    title: str
    channel: str
    tx_date: date
    slot_start: str            # "21:00"
    audience: float            # raw BARB audience (000s) - CONFIDENTIAL
    share: float               # raw share % - CONFIDENTIAL
    slot_avg_share_8wk: float  # trailing 8-week average share for this channel-slot
    vod_uplift_pct: Optional[float] = None  # consolidated/VOD uplift vs overnight


@dataclass
class SeriesMetrics:
    """Derived, percentile-normalised scores (0-100). Safe to cross the firewall."""
    series_id: str
    title: str
    channel: str
    computed_at: date
    momentum: float            # spec 4.1
    loyalty: float             # spec 4.2
    reach: float               # spec 4.3
    streak_weeks: int
    episodes_measured: int
    series_complete: bool = False
    completed_on: Optional[date] = None


@dataclass
class ScheduleItem:
    """A forward PA schedule entry, post ID-matching."""
    pa_id: str
    series_id: Optional[str]   # None if unmatched -> excluded from selection
    title: str
    channel: str
    tx: datetime
    genre: str
    is_live_event: bool = False
    availability: list[str] = field(default_factory=list)
    image_ref: Optional[str] = None
    is_new_series: bool = False


@dataclass
class ClusterAffinity:
    """Affinity lift of a series for a taste cluster. Spec 4.4-4.5."""
    series_id: str
    cluster_id: str
    lift: float
    cell_size: int             # panel homes behind the estimate


@dataclass
class AlertItem:
    """A selected alert. Contains ONLY derived descriptors - no raw figures."""
    alert_type: AlertType
    series_id: str
    title: str
    channel: str
    tx: Optional[datetime]
    availability: list[str]
    image_ref: Optional[str]
    evidence: dict             # qualitative bands + streaks only (spec 6)
    score: float               # internal ranking score, never published


@dataclass
class Edition:
    """One day's send for one segment."""
    edition_date: date
    cluster_id: str
    items: list[AlertItem]
    quiet_day: bool = False
