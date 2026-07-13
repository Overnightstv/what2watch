import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

import pytest
from overnight.models import EpisodeRecord


def eps(series_id, title, audiences, channel="ITV1", share=None, slot_avg=10.0,
        start=date(2026, 6, 1), vod=None):
    """Build weekly EpisodeRecords from a list of audiences."""
    out = []
    for i, a in enumerate(audiences):
        out.append(EpisodeRecord(
            programme_id=f"{series_id}-e{i+1}", series_id=series_id, title=title,
            channel=channel, tx_date=date.fromordinal(start.toordinal() + 7 * i),
            slot_start="21:00", audience=a,
            share=(share[i] if share else a / 400.0),
            slot_avg_share_8wk=slot_avg,
            vod_uplift_pct=(vod[i] if vod else None),
        ))
    return out


@pytest.fixture
def universe():
    """Synthetic market: a hit, a grower (hidden gem shape), a decliner, filler."""
    u = {
        "hit": eps("hit", "Coldwater", [7000, 7400, 7900], slot_avg=8.0),
        "gem": eps("gem", "Harbour Lights", [900, 1000, 1150, 1300], channel="Channel 5",
                   slot_avg=3.0, vod=[0.4, 0.45, 0.5, 0.5]),
        "fade": eps("fade", "Marlow", [3000, 2500, 2100, 1800], channel="Channel 4"),
        "steady": eps("steady", "The Summit", [4200, 4150, 4180, 4160], channel="BBC One"),
        "filler1": eps("filler1", "Quiz Thing", [2000, 2000]),
        "filler2": eps("filler2", "Old Repeats", [800, 750]),
    }
    return u
