# Design: Domain Extraction and Decision-Layer Tests

**Branch**: `refactor/domain-extraction-and-decision-tests` · **Baseline**: 25 tests green (`pytest tests/`)

One paragraph summary: split the 3640-line `ea_analyzer.py` router into a web layer plus a new `incubation_domain.py` module, move the trade matcher into a new shared leaf module `trade_matching.py` (fixing the validator's exact-equality matching bug), delete 281 lines of verified dead code first, and pin the 1610-line decision layer with characterization tests before anything moves. Behavior-preserving throughout, except one deliberate, tested behavior change: the matcher fix.

## Goals

- Screaming, testable module layout: decision logic and incubation domain importable without Flask.
- Fix the matching inconsistency (dashboard vs validator) with a regression test.
- Test coverage on the decision layer where a bug costs real money.
- Remove dead code and doc drift.

## Non-Goals

- No blueprints, no `app/` package restructure, no ports/adapters, no DI container, no repository interfaces. This is a local single-user tool.
- No behavior changes to scoring, verdicts, or thresholds.
- No template or UI changes.

## Target Module Layout

```
trade_matching.py        NEW leaf (stdlib only): normalize_trade_key, trade_matches_ea
metrics.py               unchanged leaf
incubation_validator.py  unchanged leaf (decision engine)
local_json.py            unchanged leaf
validator.py             imports metrics + trade_matching (bug fix)
incubation_domain.py     NEW: ~1,116 lines moved from ea_analyzer.py 572-1915 region
                         + constants ea_analyzer.py 431-571
                         imports: incubation_validator, trade_matching, stdlib
ea_analyzer.py           web layer: Flask routes, session/cache, url_for glue
                         imports validator, incubation_domain, trade_matching
```

Import graph stays acyclic:

    trade_matching   metrics   incubation_validator      (leaves)
         ▲   ▲          ▲            ▲
         │   └─ validator            │
         │         ▲       incubation_domain
         │         │            ▲
         └──── ea_analyzer ─────┘

## Decisions

### D1 — Matcher lives in a new `trade_matching.py`, not `metrics.py`

**Choice**: dedicated leaf module `trade_matching.py` exporting `normalize_trade_key` and `trade_matches_ea`.
**Rejected**: putting it in `metrics.py` (validator already imports it — zero new imports).
**Rationale**: `metrics.py`'s contract is "performance metrics from net P&L". Trade-to-EA identity resolution is a different concern; hiding it in metrics buys one saved import line at the cost of cohesion. A ~30-line dedicated module screams its purpose, keeps the fix reviewable in isolation, and is trivially importable by `validator.py`, `ea_analyzer.py`, and `incubation_domain.py` without any cycle (verified: `validator.py` imports only `local_json` + `metrics`; `incubation_validator.py` is stdlib-only).

### D2 — Incubation domain lives in a single flat `incubation_domain.py`

**Choice**: one flat module, following the repo's existing flat-file convention (`validator.py`, `incubation_validator.py`, `metrics.py`).
**Rejected**: an `incubation/` package (`incubation/domain.py`, `incubation/validator.py`) — over-engineering for a flat repo and churns `incubation_validator` imports for no behavioral gain. Also rejected: multiple small modules (forms/rows/timeline) — the block is cohesive and ~1,100 lines is acceptable for a domain module; split later only if it earns it.
**Rationale**: screaming architecture is achieved by the name and the Flask-free import contract, not by directory depth.

### D3 — Rename underscore-private names to public API: YES, in a separate mechanical commit

**Choice**: when a function crosses the module boundary it becomes public. Drop `_` and the redundant `_incubation_` prefix (the module name carries that context): `_incubation_build_comparison_rows` → `build_comparison_rows`, `_trade_matches_ea` → `trade_matches_ea`, etc. Do the move verbatim first (move-only diff, verifiable with `git diff --color-moved`), then rename in an immediately following mechanical commit with an old→new mapping table in the message.
**Rejected**: (a) keep `_foo` names exported across modules — permanent smell, misleads every future reader about the contract; (b) rename during the move — makes the large move diff unverifiable as a pure move.
**Rationale**: call sites are all in Python (routes), none in templates (verified — templates consume the computed dicts, not the functions), so churn is grep-mechanical. Naming note: keep domain-layer `checkpoint_for_trades` distinct from `incubation_validator.get_checkpoint_for_trades` (module qualification disambiguates).

### D4 — Dead code deletion BEFORE extraction

**Choice**: delete the ~281 verified-unreferenced lines first, as a pure-deletion commit.
**Rejected**: deleting after the move.
**Rationale**: (a) 281 fewer lines to move and review; (b) the shadowed `_incubation_build_comparison_rows` at 1407 is a landmine — moving both defs preserves an order-sensitive rebinding trap and the dead one reads the stale pre-dual-MC `entry["monte_carlo"]` key; resolve it up front in a trivially reviewable deletion; (c) deletion commits are cheap to review and revert.

### D5 — `_build_incubation_dashboard` stays whole in the web layer

**Choice**: do not split. It remains in `ea_analyzer.py`.
**Rejected**: extracting a pure core and injecting a `url_builder` callable.
**Rationale**: it is view-model glue — `url_for` at 853/860 and session-backed data loading are its essence. A pure core would have exactly one consumer (its own wrapper) and near-zero independent test value; the valuable logic it calls (comparison rows, verdict cards) is tested in `incubation_domain`. Injecting URL builders is ceremony this tool has not earned.

### D6 — `parsed_data` injection: FOR

**Choice**: inject `parsed_data` as a parameter into `_incubation_load_ea_metrics`; refactor `_incubation_evaluate_ea` to take `parsed_data` and the store entry, returning the result plus updated entry — the route performs `load/save_incubation_store` I/O.
**Rejected**: leaving them session-coupled in the web layer (splits the domain into three non-contiguous runs and leaves two evaluation functions untestable).
**Rationale**: a 4-call-site change that makes both functions pure, merges runs B/C/D into one contiguous extractable block, and puts evaluation — decision-adjacent code — under test. I/O stays at the edge (route), which is exactly where a hexagonal instinct says it belongs, achieved without any interface ceremony.

### D7 — Characterization tests FIRST, extraction in verified slices

**Choice**: pin behavior before moving anything. `validator.py` and `incubation_validator.py` do not move, so tests written now survive the whole refactor untouched. For the moved block: write characterization tests importing from `ea_analyzer` pre-move; after the move only the import line changes — the green suite is the proof of behavior preservation. Extract in slices (constants → functions), running the full suite plus `python -c "import ea_analyzer"` and a manual smoke (dashboard + one strategy page) between slices.
**Optional seam**: extract the 6-line CP3 verdict mapping (incubation_validator.py:832-848) into `_resolve_cp3_verdict(score, below_mc95, cp2_verdict)` so exact boundaries (65/45) are directly testable instead of reverse-engineering fixture scores. Mechanical, done under the freshly pinned suite.

### D8 — Matcher fix is its own commit, never smuggled into the refactor

**Choice**: commit A moves the matcher verbatim (behavior-preserving); commit B switches `validator.get_all_validator_results` from `t.get("comment") == ea_name` (validator.py:574-578) to `trade_matches_ea(t, ea_name, config)` with the regression test. Note: this requires passing `config` (mappings) into the filter — the validator currently iterates `mappings` already, so the mapping dict is in scope.
**Rationale**: the fix is a deliberate behavior change (EAs previously showing zero validator trades will now score). It must be findable in `git log`, revertable alone, and carry its test.

## Testing Strategy (priority order)

| # | Behavior pinned | Concrete cases |
|---|-----------------|----------------|
| 1 | **Matcher regression** (the money bug) | Unit: `normalize_trade_key("USDJPY 1104") == "usdjpy1104"`; comment/alias/magic match paths. Integration: `get_all_validator_results` with a closed trade `comment="USDJPY 1104"` and mapping key `"USDJPY_1104"` (magic set) → result has `total_trades > 0`; same fixture against old equality logic yields 0 (documented in test docstring). |
| 2 | **CP3 verdict boundaries** (incubation_validator.py:832) | Via `_resolve_cp3_verdict` seam: `(65.0, [], None) → APROBAR`; `(64.99, [], None) → OBSERVAR`; `(45.0, [], None) → OBSERVAR`; `(44.99, [], None) → ELIMINAR`. |
| 3 | **below_mc95 gate** | score ≥65 with one metric live < mc95 → `OBSERVAR`, never `APROBAR`; `avg_trade` alone below mc95 does NOT trigger the gate (dedupe with expectancy, line 814); metric with `mc95=None` is skipped. |
| 4 | **CP2→CP3 escalation** | `previous_cp2_result={"verdict": "OBSERVAR"}` + CP3 lands OBSERVAR → `ELIMINAR`, `escalation_from_cp2=True`. |
| 5 | **`get_worst_case_mc` dual-MC selection** | both sources: higher-is-better metric picks the LOWER value, DD-style picks the HIGHER; manipulation-only → manipulation values; both empty → empty. Test 95 and 50 confidence levels. |
| 6 | **`dd_estado` N/D truth** (validator.py:265-297) | `max_dd_live=None` → N/D; BT worst-DD path OK/ALERTA(≤1.5×)/FUERA boundaries; only ONE of `mc_r_dd`/`mc_t_dd` present and no BT DD → N/D (this is the test that contradicts docs/decision-logic.md's phantom fallback); both MC present → OK ≤ min, ALERTA ≤ max, FUERA above. |
| 7 | **Checkpoint thresholds** | trades 4/5, 19/20, 39/40 → PRE_CP1/CP1/CP2/CP3. |
| 8 | **Characterization of moved block** | `build_comparison_rows` (the live, manipulation/retest-aware def at 1527) with a dual-MC fixture entry; `verdict_card`, `timeline_from_entry`, `compute_spp_ratios` smoke-level golden assertions. |

## File Changes

| File | Action | Description |
|------|--------|-------------|
| `trade_matching.py` | Create | `normalize_trade_key`, `trade_matches_ea` moved from ea_analyzer.py:350-367 |
| `incubation_domain.py` | Create | constants 431-571 + pure block from region 572-1915 (minus web functions 766-785, 813-944; minus dead code) |
| `ea_analyzer.py` | Modify | delete dead code; delete moved code; import new modules; routes pass `parsed_data`/store entry |
| `validator.py` | Modify | use `trade_matches_ea` in `get_all_validator_results` (behavior fix) |
| `incubation_validator.py` | Modify | extract `_resolve_cp3_verdict` seam only |
| `tests/test_decision_*.py`, `tests/test_trade_matching.py`, `tests/test_incubation_domain.py` | Create | per table above |
| `AGENTS.md`, `docs/decision-logic.md`, `docs/backend.md`, `requirements.txt` | Modify | fix CP3 gate wording, remove phantom DD fallback, `_get_metrics_cached()` name, drop unused `pandas` |

## Migration Sequence / Commit Slices

Each commit is a work unit: green suite, revertable alone. Total change greatly exceeds 400 authored lines → **chained PRs** (Decision needed before apply: Yes · Chained PRs recommended: Yes · 400-line budget risk: High).

| PR | Commit | Content |
|----|--------|---------|
| 1 | `test(decision): characterization tests for validator + incubation_validator` | cases 2-7 above; includes `_resolve_cp3_verdict` seam |
| 1 | `refactor: delete dead incubation code` | the 7 verified-dead items, ~281 lines, pure deletion |
| 2 | `refactor: extract trade matcher to trade_matching.py` | move + rename + matcher unit tests; behavior-preserving |
| 2 | `fix(validator): match trades by normalized comment/alias/magic` | validator switch + USDJPY 1104 regression test; docs note |
| 3 | `refactor: inject parsed_data into incubation evaluation helpers` | D6; small diff, suite green |
| 3 | `refactor: extract incubation domain to incubation_domain.py` | verbatim move (review with `--color-moved`); characterization tests re-pointed |
| 3 | `refactor(incubation): rename extracted helpers to public API` | mechanical, mapping table in message |
| 4 | `docs: fix decision-logic drift and drop unused pandas` | Finding 4 items |

PR 3's move commit will exceed 400 lines by nature; it is mechanical and reviewed as a move, not read line-by-line — flag `size:exception` per delivery strategy if required.

## Risks

- **No test net under moved code**: mitigated by D7 (pin first, move verbatim, re-point imports); residual risk on the six web functions left behind — covered by route smoke tests only.
- **Matcher fix widens matching in the validator**: an EA could now pick up trades from a *different* EA whose comment normalizes to the same key (e.g. `"EA-1"` vs `"EA 1"`). Accepted: this is exactly the dashboard's existing semantics — one truth beats two. Regression test documents it.
- **Hidden dynamic references to "dead" code**: verified none (`getattr`/`globals` audit done), but the deletion commit is trivially revertable if a template regression appears in smoke.
- **Lazy imports in the moved block** (`defaultdict` at 1246, `get_worst_case_mc` at 1541): hoist to module top of `incubation_domain.py` during the move; behavior-identical, but note it in the move commit so the diff deviation is explained.
- **`_incubation_evaluate_ea` store refactor** (D6) is the only moved function whose signature changes semantically (I/O hoisted to route); it gets a dedicated characterization test before and after.

## Threat Matrix

N/A — no routing, shell, subprocess, VCS/PR automation, executable-file classification, or process-integration boundary changes.

## Open Questions

- [ ] None blocking. `size:exception` acceptance for PR 3's move commit is a delivery-strategy call at apply time, not a design blocker.
