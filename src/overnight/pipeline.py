"""Nightly orchestration. Spec section 2.

Run once per day after overnights land (~10:00 BST).
1. Ingest trailing 28 days of BARB data -> EpisodeRecords
2. Compute series metrics
3. Fetch PA forward schedule + ID matching
4. Select edition (single cluster for MVP)
5. Generate copy via Claude
6. Send edition email (or lint-blocked alert)
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from overnight.ingest import ingest_trailing_window
from overnight.metrics.scores import compute_series_metrics
from overnight.pa_schedule import build_schedule, CATCHUP
from overnight.models import EpisodeRecord, ScheduleItem
from overnight.selection.engine import SelectionEngine
from overnight.copygen.generate import generate_copy
from overnight.deliver import send_edition, send_lint_alert

CFG = yaml.safe_load(
    (Path(__file__).parents[2] / "config" / "thresholds.yaml").read_text()
)


def _synthetic_schedule(
    universe: dict[str, list[EpisodeRecord]],
    today: datetime,
) -> list[ScheduleItem]:
    """Build a schedule from the BARB universe without calling PA.

    Takes the most recent episode of each series and projects it as if
    airing tonight at its usual slot time. Used when --no-pa is set.
    """
    items = []
    for sid, eps in universe.items():
        latest = eps[-1]
        try:
            h, m = latest.slot_start.split(":")
            tx = today.replace(hour=int(h), minute=int(m), second=0, microsecond=0,
                               tzinfo=timezone.utc)
        except Exception:
            tx = today.replace(hour=21, minute=0, second=0, microsecond=0,
                               tzinfo=timezone.utc)
        items.append(ScheduleItem(
            pa_id        = sid,
            series_id    = sid,
            title        = latest.title,
            channel      = latest.channel,
            tx           = tx,
            genre        = "",
            availability = [CATCHUP.get(latest.channel, "")],
        ))
    return items


def run_nightly(now: datetime | None = None, dry_run: bool = False, no_pa: bool = False) -> None:
    now   = now or datetime.now(timezone.utc)
    today = now.date()

    print(f"\n── What 2 Watch pipeline  {today.isoformat()} ─────────────────\n")

    # 1. BARB ingest — trailing 28 days
    print("Step 1: Ingesting BARB data…")
    universe = ingest_trailing_window(days=28, today=today)
    if not universe:
        print("  No episode data returned — aborting.")
        sys.exit(1)

    # 2. Series metrics
    print("\nStep 2: Computing series metrics…")
    metrics = {
        sid: compute_series_metrics(eps, universe, CFG, computed_at=today)
        for sid, eps in universe.items()
    }
    print(f"  {len(metrics)} series scored")

    # 3. Forward schedule — PA API or synthetic fallback
    if no_pa:
        print("\nStep 3: Building synthetic schedule (--no-pa)…")
        schedule = _synthetic_schedule(universe, now)
        print(f"  {len(schedule)} synthetic items from BARB universe")
    else:
        print("\nStep 3: Fetching PA forward schedule…")
        schedule = build_schedule(today, universe, lookahead_days=7)
        if not schedule:
            print("  No schedule items — aborting.")
            sys.exit(1)

    # 4. Selection — single cluster for MVP
    print("\nStep 4: Running selection engine…")
    engine     = SelectionEngine(CFG, history=[])
    candidates = engine.build_candidates(schedule, metrics, now)
    edition    = engine.allocate(candidates, edition_date=today, cluster_id="default")

    if edition.quiet_day:
        print(f"  Quiet day — only {len(edition.items)} item(s) selected.")
    else:
        print(f"  {len(edition.items)} item(s): "
              + ", ".join(a.title for a in edition.items))

    if not edition.items:
        print("  Nothing to send today.")
        return

    # 5. Copy generation + lint
    print("\nStep 5: Generating copy…")
    result = generate_copy(edition)

    if result["status"] == "blocked":
        print(f"  ✗ Blocked by lint: {result['lint']}")
        if not dry_run:
            send_lint_alert(edition, result["lint"], today)
        return

    copy = result["copy"]
    print(f"  ✓ \"{copy.get('subject_line')}\"")

    # 6. Send
    if dry_run:
        print("\n── DRY RUN ──────────────────────────────────────────────────")
        print(f"Subject: {copy.get('subject_line')}")
        for item in copy.get("items", []):
            print(f"\n  [{item.get('chip')}] {item.get('headline')}")
            print(f"  {item.get('body')}")
        print(f"\nWhatsApp:\n{copy.get('whatsapp_compact')}")
        print("─" * 60)
    else:
        print("\nStep 6: Sending edition…")
        send_edition(edition, copy, today)

    print("\n── Done ─────────────────────────────────────────────────────\n")


if __name__ == "__main__":
    dry   = "--dry-run" in sys.argv
    no_pa = "--no-pa"   in sys.argv
    run_nightly(dry_run=dry, no_pa=no_pa)
