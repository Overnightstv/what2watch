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

import pickle
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
from overnight.deliver import send_edition, send_lint_alert, draft_subscriber_editions
from overnight.clustering.genre_classifier import build_cluster_index, classify_series

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


CACHE_PATH = Path(__file__).parents[2] / "data" / "universe_cache.pkl"


def run_nightly(now: datetime | None = None, dry_run: bool = False, no_pa: bool = False, cache: bool = False) -> None:
    now   = now or datetime.now(timezone.utc)
    today = now.date()

    print(f"\n── What 2 Watch pipeline  {today.isoformat()} ─────────────────\n")

    # 1. BARB ingest — trailing 28 days (or load from cache)
    print("Step 1: Ingesting BARB data…")
    if cache and CACHE_PATH.exists():
        print(f"  Loading from cache: {CACHE_PATH}")
        with open(CACHE_PATH, "rb") as f:
            universe = pickle.load(f)
        print(f"  {len(universe)} series loaded from cache")
    else:
        universe = ingest_trailing_window(days=28, today=today)
        if not universe:
            print("  No episode data returned — aborting.")
            sys.exit(1)
        if cache:
            CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(CACHE_PATH, "wb") as f:
                pickle.dump(universe, f)
            print(f"  Cache saved → {CACHE_PATH}")

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

    # 4. Build cluster index — which series belong to which interest cluster
    print("\nStep 4: Building cluster index…")
    cluster_index = build_cluster_index(universe)
    clusters = CFG.get("clusters", {})
    print(f"  {len(clusters)} clusters: {', '.join(clusters)}")
    for cid, sids in cluster_index.items():
        print(f"    {cid}: {len(sids)} series")

    # 5. Selection — one edition per cluster
    print("\nStep 5: Running selection engine per cluster…")
    engine = SelectionEngine(CFG, history=[])
    all_candidates = engine.build_candidates(schedule, metrics, now)

    editions = {}
    for cluster_id in clusters:
        cluster_series = set(cluster_index.get(cluster_id, []))
        # Filter candidates to only those in this cluster
        cluster_candidates = [c for c in all_candidates if c.series_id in cluster_series]
        edition = engine.allocate(cluster_candidates, edition_date=today, cluster_id=cluster_id)
        editions[cluster_id] = edition
        label = clusters[cluster_id]["label"]
        if edition.quiet_day:
            print(f"  {label}: quiet day ({len(edition.items)} item(s) qualify)")
        else:
            print(f"  {label}: {len(edition.items)} item(s) — "
                  + ", ".join(a.title for a in edition.items))

    # 6. Copy generation + delivery per cluster
    print("\nStep 6: Generating copy…", flush=True)
    editions_with_copy: dict = {}
    for cluster_id, edition in editions.items():
        label = clusters[cluster_id]["label"]
        if not edition.items:
            if dry_run:
                print(f"\n── {label.upper()} — no send today ──")
            continue

        result = generate_copy(edition)

        if result["status"] == "blocked":
            print(f"  ✗ {label}: blocked by lint — {result['lint']}")
            if not dry_run:
                send_lint_alert(edition, result["lint"], today)
            continue

        copy = result["copy"]
        print(f"  ✓ {label}: \"{copy.get('subject_line')}\"")
        editions_with_copy[cluster_id] = (edition, copy)

        if dry_run:
            print(f"\n── {label.upper()} ──────────────────────────────────────────────")
            print(f"Subject: {copy.get('subject_line')}")
            for item in copy.get("items", []):
                print(f"\n  [{item.get('chip')}] {item.get('headline')}")
                print(f"  {item.get('body')}")
            print(f"\nWhatsApp:\n{copy.get('whatsapp_compact')}")
            print("─" * 60)
        else:
            send_edition(edition, copy, today)

    if not dry_run and editions_with_copy:
        print("\nStep 7: Saving subscriber drafts…", flush=True)
        draft_subscriber_editions(editions_with_copy, today)

    print("\n── Done ─────────────────────────────────────────────────────\n")


if __name__ == "__main__":
    dry   = "--dry-run" in sys.argv
    no_pa = "--no-pa"   in sys.argv
    cache = "--cache"   in sys.argv
    try:
        run_nightly(dry_run=dry, no_pa=no_pa, cache=cache)
    except Exception as exc:
        import traceback
        traceback.print_exc()
        print(f"\n✗ Pipeline failed: {exc}", flush=True)
        sys.exit(1)
