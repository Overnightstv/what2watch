# overnight. — alert-selection engine

Scaffold implementing the *Alert-selection engine — technical specification v1.0*.
Everything here runs and is tested against synthetic BARB-shaped data; the
`TODO(wiring)` markers in `src/overnight/pipeline.py` are the only points that
need connecting to live systems (BARB store, PA feed, subscriber DB, ESP/WhatsApp).

## Map to the spec

| Module | Spec section |
|---|---|
| `src/overnight/models.py` | 3, 5, 6 — data models; the raw/derived firewall |
| `src/overnight/metrics/scores.py` | 4.1–4.3 — momentum, loyalty, reach, bands |
| `src/overnight/metrics/affinity.py` | 4.4–4.5 — lift, clusters, minimum cell floor |
| `src/overnight/matching/id_match.py` | 3.1 — PA↔BARB identity resolution |
| `src/overnight/selection/engine.py` | 5 — gates, slot allocation, dedup, quiet-day |
| `src/overnight/copygen/generate.py` | 7 — production prompt + Anthropic harness |
| `src/overnight/copygen/lint.py` | 6 — post-generation no-figures checker |
| `config/thresholds.yaml` | 4–5, 9 — every tunable in one reviewable file |
| `templates/` | daily email + WhatsApp render shells |

## Run the tests

```bash
pip install -r requirements.txt
python -m pytest tests/ -q     # 16 tests, all passing
```

## Wiring order (suggested, in Claude Code)

1. **BARB ingest** → produce `EpisodeRecord`s from the existing Overnights.tv
   store; run `compute_series_metrics` over a real trailing window and eyeball
   the bands against your own knowledge of what's hot.
2. **PA schedule + ID matching** → build the `IdMatcher` barb_index from the
   store; measure the auto-match rate (target ≥98% primetime, spec 9).
3. **Selection dry-runs** → run the engine daily for a week, printing editions
   without sending; tune `config/thresholds.yaml` until the picks look right.
4. **Copy generation** → `ANTHROPIC_API_KEY` is already configured in your
   Claude Code environment; generate real editions, review lint behaviour.
5. **Respondent-level affinity** → weekly batch producing cluster lifts;
   the 50-home floor in config is a hard compliance control — do not lower it.
6. **Delivery** → render templates, human-review queue, 17:30 send.

## Non-negotiables carried from the spec

- Raw audiences/shares never leave the metrics layer (`models.py` docstring).
- Unmatched PA items are excluded, never guessed.
- Any lint hit blocks to human review; nothing auto-publishes on a violation.
- The affinity cell floor (50/30) is statistical validity *and* panel-privacy
  compliance in one constant. Treat it as immutable.
- Licence confirmations (PA consumer redistribution; BARB derived consumer use;
  respondent-level recommendation use) are pre-launch blockers — spec §6.
