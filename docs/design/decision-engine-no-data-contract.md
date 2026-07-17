# Design: Explicit SIN DATOS Contract for the Decision Engines

Branch: `audit/decision-engine-correctness`. Source: Judgment Day ledger (10 CRITICALs, all empirically proven) and the user decision: **missing reference data must produce an explicit SIN DATOS state that names what is missing — never a confident verdict, never a silent default.** Rejected alternatives: exclude-and-renormalize, conservative worst-case defaults (the status quo).

## 1. The SIN DATOS contract (both engines)

### validator.py — reuse `_nd_result`, extend it

`_nd_result(veredicto, accion)` already produces the target shape (`veredicto="SIN DATOS"`, `sin_datos=True`, `score=None`, all estados `"N/D"`). Extend it with one field and one new call site:

- Add `result["missing"] = [..]` (empty list in the existing <5-trades guard and in normal results).
- After extracting inputs and BEFORE any estado/scoring logic, collect missing required inputs (Section 2). If non-empty: `return _nd_result("SIN DATOS", "Completar datos de referencia: " + ", ".join(missing))` with `missing` attached.
- The existing <5-trades SIN DATOS/ELIMINAR guard runs FIRST and is unchanged.

### incubation_validator.py — new `_sd_result` mirroring `_nd_result`

Each `evaluate_cp{1,2,3}` and the PRE_CP1 branch gains a completeness gate before computing anything. On missing data it returns:

```
{"checkpoint": <CP>, "verdict": "SIN DATOS", "score": None, "sin_datos": True,
 "missing": ["mc95.max_consec_losses", ...],
 "gates": {}, "hard_gate_failures": [], "metrics_evaluation": {}, "metrics_scores": {},
 "category_scores": {}, "spp_adjustments": [], "escalation_from_cp2": False,
 "mc_source": {"has_manipulation": ..., "has_retest": ..., "dominant_metrics": {}}}
```

`evaluate_incubation` passes `verdict`, `score=None`, `missing`, and `sin_datos` through to its envelope. `incubation_domain._incubation_sync_checkpoint_store` MUST NOT write SIN DATOS evaluations into `checkpoints.cp1/cp2/cp3` slots (only `last_evaluation`), so timelines stay clean and the CP2→CP3 anti-limbo rule never consumes a SIN DATOS as a prior CP2 verdict.

## 2. Required-set per evaluation path

A field is required iff its absence would otherwise alter the verdict through a default. Order of the `missing` list: live → backtest → mc95 → mc50 → spp → start_date (stable, deterministic).

| Path | Required inputs |
|---|---|
| validator (all) | `live.win_rate/profit_factor/payout_ratio/expectancy/max_dd_pct/max_consec_losses/stagnation_days/avg_bars`; `bt.win_rate/profit_factor/payout_ratio/avg_bars/max_consec_losses/trades_total/months`; DD path: `bt.worst_dd_1m` OR (`mc_retest.max_dd` AND `mc_trades.max_dd`); `spp.expectancy_median` |
| validator (optional) | `bt.stagnation_days` (documented absolute fallback 60/120 days stays); `bt.expectancy` (`live_vs_bt_profit_ratio` is informational, stays N/D-tolerant) |
| PRE_CP1 | start date (see below); `backtest.total_trades` + parseable `backtest.bt_period` (or `backtest.monthly_frequency`). Unparseable/absent → SIN DATOS naming `backtest.bt_period` — no more perpetual PENDING |
| CP1 hard gates | `backtest.win_rate`; `mc95.max_dd_pct`; `mc95.max_consec_losses`; live dd/mcl/wr/wins. "mc95.X" = key present in the worst-case bundle, i.e. in at least one provided MC95 section. Frequency check is a WARNING, not a gate: missing period → `frequency.status="SIN DATOS"`, non-blocking |
| CP2 | CP1 set + per-metric `mc95.<k>` AND `mc50.<k>` for the 7 metrics + the 7 live values. MC50 sections are form-optional but verdict-mandatory: absent MC50 → SIN DATOS naming the `mc50.*` keys (user decision explicitly lists "MC50 section absent" as missing data) |
| CP3 | CP2 set extended to the 9 scored metrics, + `bt.<k>` per metric, + coherence inputs (`backtest.total_trades`, parseable `bt_period`) — coherence carries 15% weight, so it cannot silently score 10 |
| SPP (incubation) | Genuinely optional. SPP only ever upgrades (CP2 rescue, CP3 15% blend); absence never manufactures a verdict from defaults, so verdicts on complete BT+MC data remain confident. This asymmetry vs the validator (where SPP feeds a weighted estado) is intentional |

**Incubation start date (fixes C7)**: `days_incubating` = days since `entry["date_added"]` (already written at reference save, `ea_analyzer.py:1134`), not since first trade. A zero-trade EA now ages and hits the PRE_CP1 frequency deadline. Migration in `migrate_incubation_store()`: backfill missing `date_added` with first-trade date, else today. `actual_monthly` uses the same denominator. First-trade date remains only a legacy fallback.

## 3. Missing-list format

Dotted `section.field` strings: `live.*`, `backtest.*` (incubation) / `bt.*` (validator, matching its store keys), `mc95.*`, `mc50.*` (worst-case-bundle view: missing means missing from every provided section at that confidence), `mc_retest.*`/`mc_trades.*`/`spp.*` (validator), `start_date`. UI action strings stay Spanish: `"Completar datos de referencia: mc95.max_consec_losses, mc50.max_dd_pct"`.

## 4. `reference_ready` and `read_field`

- **`reference_ready`: do NOT tighten.** Keeping the coarse gate (any backtest + any MC95 dict) lets evaluation run and return SIN DATOS naming exact fields — strictly more informative than the generic "NO DATA / Cargar datos de referencia" card that a tightened gate would show. Engines are the defensive layer.
- **`read_field`: fix, but as root-cause hardening, not the safety net.** Replace `if required and section_key == "backtest"` with all-or-nothing per required section: if a `required: True` section has ANY value entered, every empty field in it becomes a form error; a fully empty section stays allowed (the existing "at least one MC95" check at `parse_reference_form` remains the section-level rule, since `mc_manipulation_95`/`mc_retest_95` are either/or). Tradeoff: touches the form layer outside the audited files and changes save UX for partially filled sections; accepted because a decorative `required: True` is worse than none, and it kills C3's reachability at the source. Engine-side SIN DATOS stays regardless.

## 5. SPP orientation and wiring (fixes C9)

- **Correct orientation: `median / original`** (matches docs/decision-logic.md:300 "la mediana SPP es >=130% de la original"). Rationale: SPP measures parameter robustness; a median far BELOW the original run means the shipped parameter set is a lucky outlier (overfit). Confidence must grow when the typical permutation performs at least as well as the original.
- `compute_spp_ratios` yields `original/median` — inverted for this purpose. It stays render-only and untouched (its display label already reads as "original vs median").
- **Do NOT wire `orig_vs_median_pct`** (zero write sites, wrong orientation, would need a store migration). Instead `_spp_confidence` computes confidence directly from stored flat `spp.median_*` keys and `backtest.*`: for higher-is-better metrics `conf = median/original`; for lower-is-better metrics `conf = original/median`, so `conf > 1.3` uniformly means "typical permutation is ≥30% better than original" and the single documented threshold survives.
- This revives dead code: CP2 rescue and CP3 blend will fire for the first time. Pin with tests (Section 7) and update docs/decision-logic.md to state both the orientation and the direction-dependent inversion.

## 6. Defect-by-defect resolution (remaining CRITICALs + cheap WARNINGs)

| Defect | Fix |
|---|---|
| C1 binomial | Exact left-tail CDF in pure Python via `math.comb` (n = trade counts, cheap). Delete the scipy import and branch entirely — one deterministic code path |
| C2 `_mc_section_values` | Remove the cross-confidence fallback; absent level returns `{}`. Missing keys then surface through the required-set as `mc50.*`/`mc95.*` SIN DATOS |
| C3 `_hard_gates` | Completeness gate runs before `_hard_gates`; no `or 0` coercions survive on mc95 dd/mcl |
| C4 validator `_pts("N/D")` | Unreachable after the validator completeness gate: no scored estado can be N/D when a verdict is emitted |
| C5 `_score_metric` None→0.0 | CP3 completeness gate guarantees non-None inputs; drop the `or 0.0` coercions so any future gap fails loudly instead of scoring 100 |
| C6 CP2 `_metric_status` | Same gate; live values used raw (no 0.0 coercion) |
| C7 PRE_CP1 | `date_added`-based incubation clock (Section 2) |
| C8 `below_mc95` | Blocker condition drops `mc50 is None` — it only needs live + mc95 |
| C10 + strptime WARNING | Widen the period regex to accept `.`, `-`, `/` separators; wrap `strptime` in try/except; unparseable (incl. month 13) → None → `backtest.bt_period` in `missing`, never silent 0.0 or a crash |
| ∞ WARNING (validator) | Infinite `payout_live`/`pf_live` with finite BT ref → estado OK (zero-loss EA cannot fail a deviation check), honoring `_safe_float`'s documented intent |
| Rounding WARNING | Convention: verdict/gate comparisons consume raw floats; `round()` only at result-dict assembly. No cutoff moves |
| Not fixed (harmless) | `sample_score` else:40 dead branch; `_mc_source_bundle` non-directional default (unreachable for real metric keys) — documented, unchanged |

## 7. Template and presentation impact

- Unknown verdict strings do NOT break rendering: all three `verdict_class` maps fall back to `verdict-pending`. Still, add `"SIN DATOS": "verdict-no-data"` (CSS class already exists) to `incubation_domain.py:784` (`build_verdict_card`), `incubation_domain.py:1000` (`build_timeline_from_entry`), `ea_analyzer.py:563` (dashboard rows).
- **Real breakage**: `metric_summary_for_tooltip` (`incubation_domain.py:561-570`) formats `score:.2f` when `current_checkpoint == "CP3"` — a CP3 SIN DATOS (score None) raises TypeError. Guard for None; return `"SIN DATOS: <n> campos faltantes"`.
- `build_verdict_card` gains a SIN DATOS reading branch (icon ℹ, tone neutral, message listing `missing`).
- `incubation_strategy.html` / dashboard score cells already guard `score is not none` / isinstance — no changes.
- Dashboard summary counters: add a `sin_datos_count`; SIN DATOS must not count as ELIMINAR or PENDING.
- `templates/validator.html` already renders `sin_datos` results; the new `missing` list renders inside the existing `accion` string — no template change required.

## 8. Test plan — regression pins (proven flips as fixtures)

1. Binomial exact: (w=2, n=10, p=.5) → 0.0547 ≥ 0.03 PASS (shipped approx 0.0289 flipped it); (w=1, n=6, p=.6) → 0.04096 PASS; plus the 32-combo sweep asserting exact CDF == `math.comb` sum for all audited (n, w, p).
2. `_mc_section_values`: section with only `confidence_95` → requesting `confidence_50` returns `{}` (no aliasing); CP3 with MC50 absent → SIN DATOS listing `mc50.*`, never a 65-band score.
3. Hard-gate partials: entry with mc95 `max_dd_pct` only → SIN DATOS naming `mc95.max_consec_losses` (was ELIMINAR); mirror case (mcl filled, dd blank) → names `mc95.max_dd_pct`.
4. Validator flip pin: the audited fixture (MONITOREAR 63.1 with full refs) with dd/edge refs removed → SIN DATOS + missing list (was ELIMINAR 38.1); with full refs → still 63.1 MONITOREAR.
5. CP3 missing reference never scores 100: absent bt/mc for a higher-is-better metric → SIN DATOS (was perfect score).
6. CP2 partial MC → SIN DATOS not ELIMINAR; healthy full-data CP2 fixture → CONTINUAR unchanged.
7. PRE_CP1 zero-trade EA, `date_added` 400 days ago, bt_monthly ≈ 5 → deadline exceeded → ELIMINAR (was perpetual PENDING); fresh `date_added` → PENDING; unparseable bt_period → SIN DATOS.
8. `below_mc95` blocker: live_dd 14 vs mc95_dd 10, full data → APROBAR blocked → OBSERVAR (proven flip pinned).
9. bt_period parsing: `2024/01/02 - 2025/01/02` parses; `2024.13.01 - 2025.01.01` → None, no crash.
10. SPP: median ≥130% of original + live within median → CP2 rescue fires and CP3 blend shifts the score (first-ever activation pins); SPP absent → no adjustment AND a confident verdict still emitted; lower-is-better inversion pinned.
11. Zero-loss EA: `payout_ratio = "∞"` → payout_estado OK, not FUERA.
12. SIN DATOS persistence: checkpoint slots never store SIN DATOS; anti-limbo rule unaffected.
13. Tooltip/verdict-card render with CP3 SIN DATOS (no TypeError); `verdict_class == "verdict-no-data"` in card, timeline, dashboard row.

## 9. Explicitly NOT changing (verified correct in the audit)

- All weight sums: validator 35+30+15+20=100 with sub-weights 100 each; CP3 deviation 1.00, risk 1.00, categories 0.45+0.30+0.15+0.10=1.00.
- `_score_metric` piecewise interpolation, continuous at 25/65/100 boundaries.
- Binomial LEFT-tail direction.
- Dual-MC worst-case direction (min for higher-is-better, max for lower-is-better).
- Checkpoint boundaries 5/20/40; verdict cutoffs 45/65/70.
- Validator stagnation factors 0.3/0.6.

Tests in Section 8 pin each of these so the SIN DATOS work cannot regress them.
