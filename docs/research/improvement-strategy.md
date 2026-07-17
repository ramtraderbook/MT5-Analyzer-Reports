# P-C — Improvement Strategy for MT5-Analyzer-Reports

**Type:** strategy document, not an implementation.
**Purpose of the tool it serves:** help a discretionary trader decide, with statistical
discipline, whether an EA should **keep running, be watched, or be killed** — for live EAs
(`validator.py`: CONTINUAR / MONITOREAR / ELIMINAR) and for strategies in incubation
(`incubation_validator.py`: APROBAR / OBSERVAR / ELIMINAR).
**Inputs used:** the codebase as it stands today, `docs/known-issues.md` (JD-1…JD-6 + P-A
oracle §14), and `docs/research/prior-art.md` (P-B).

A note before the four sections, because it reframes the first one:

> **The brief's premise for Section 1 is stale.** It describes `ea_analyzer.py` as 3640 lines
> with ~1100 lines of domain logic to extract. That extraction **already happened**
> (`docs/design/domain-extraction.md`, executed). The file is **2677 lines** today, the domain
> lives in separated, well-tested leaves (`incubation_domain.py` 1099, `metrics.py` 1103,
> `validator.py` 826, `trade_matching.py`, `parser.py`), and what remains in the monolith is
> the Flask/route/cache/session layer, not domain logic. Section 1 is therefore written about
> the repo as it *is*, not as the brief assumed.

The recurring theme across all four sections: **the binding constraint on this tool is not
missing algorithms — it is the absence of a single real MT5 export to calibrate against.** The
project has, correctly and on the record, refused to tune thresholds against invented fixtures
(`known-issues.md` §1). Several of the highest-value moves below are blocked on that one asset,
and I say so explicitly rather than pretending code alone unblocks them.

---

## Section 1 — Structural

### The situation, corrected

`ea_analyzer.py` = **2677 lines**. Rough shape:

| Lines (approx) | Cluster | Layer |
| --- | --- | --- |
| 1–126 | imports, `app = Flask(...)`, `.secret_key` file I/O, `os.makedirs`, globals | delivery + infra, **runs at import** |
| 128–481 | metrics TTL cache, config/store load-save, CSRF, context processors, disk-cache orchestration | infra |
| 483–825 | sidebar/view-model builders, `get_parsed_data*` (session+disk), `_build_incubation_dashboard` | app + view-model |
| 826–2663 | **every `@app.route` handler**, incl. all `/api/*` JSON endpoints | delivery/Flask (~1840 lines) |
| stranded | `_canonical_trade`, `_merge_changed_content` (inside the route block) | pure domain, Flask-free |

The domain/application/delivery separation the brief asks for **substantially exists**. The
pure decision core is Flask-free and carried by dense characterization + property + differential
oracle suites (`tests/oracle/`). What is *not* clean is the ~1840-line route layer plus the
cache/session/config infra — and that layer is the **thinly-tested** surface.

### What to actually do here — ranked by value / cost

**1A. Kill import-time side effects. (value: high, cost: low — do this first)**
`import ea_analyzer` today writes `.secret_key`, makes directories, and constructs `app` as a
side effect (lines ~80–96). That is the single barrier stopping the ~1840 route lines from being
unit-tested in isolation, and every one of the "further slicing" ideas below is unsafe until the
route layer is testable.
- *Value:* unlocks route-level testing; testing is the precondition the brief itself demands
  before any further extraction is "safe."
- *Cost:* small — move the side effects into an `create_app()` / `if __name__ == "__main__"`
  guard and a lazy secret-key accessor.
- *Risk:* low; behavior-preserving if the app entrypoint calls the factory.
- *Breaks:* any code importing `app` at module load (a handful of tests — fix them in the same PR).

**1B. Extract the disk-cache + session-coupling layer into a `storage`/`cache` module.
(value: medium, cost: medium — only after 1A)**
`_resolve_cache_path` and `_atomic_write_json` have no direct tests; `save_cache`/`load_cache`/
`cleanup_old_caches` are exercised only indirectly (one hardening test); and the
`get_parsed_data`/`get_incubation_parsed_data` session→disk readers are untested and
side-effectful. Extract them behind functions that take `cache_key` as an argument instead of
reaching into `session`, mirroring the D6 discipline already used to make `evaluate_ea` pure.
- *Value:* medium — isolates the most error-prone infra (atomic writes, cleanup deletes) and
  makes it testable.
- *Cost:* medium — needs fuller route/integration tests written *first* (current coverage of
  this glue is thin and indirect).
- *Risk:* medium — disk-mutating, order-sensitive; a bug here corrupts the runtime cache.
- *Breaks:* every route that calls `get_parsed_data`; do it as one PR with the tests.

**Migration path (each step independently shippable and reversible):**
1. Write route-level tests for the cache/session glue (green on current code). *Gate: these must
   exist and pass before 1B.*
2. 1A — factory-ize app construction and side effects. Reversible: revert one commit.
3. 1B — extract storage module with injected `cache_key`. Reversible: the module is additive;
   revert re-inlines it.
4. *(Optional, low priority)* move `_build_incubation_dashboard` and the stranded pure functions
   out — mechanical once 1A/1B land.

### What NOT to do (Section 1)

- **Do not re-run the domain decomposition.** It is done. Re-proposing it is the single biggest
  way this strategy could waste effort.
- **No blueprints, no `app/` package, no DI / ports-and-adapters.** `domain-extraction.md`
  already ruled these out as non-goals, and nothing in the current pain points argues for them.
- **Do not split the 1840-line route file for line-count aesthetics.** Splitting a monolith you
  cannot yet test trades a readable-but-covered file for several unreadable-and-uncovered ones.
  Coverage first, cosmetics never.

---

## Section 2 — Statistical depth

The primitives are genuinely good: an **exact binomial win-rate test** (`math.comb`, no normal
approximation — the one test P-B called "genuinely appropriate"), a correctly-derived
**Probabilistic Sharpe Ratio** (`calculate_psr`), a seeded **bootstrap risk-of-ruin**
(`calculate_bootstrap_risk`, 10 000 iterations), dual-MC worst-case bands, and a theoretically
justified trade-clock DD scaling. The weakness is not the math library — it is the **decision
layer**, which is a hand-tuned weighted-sum scorecard that consumes almost none of that.

Five weaknesses, with what exists today and what would strengthen each.

**2A. No uncertainty on any verdict — but the two statistics that fix it already exist, unwired.
(value: highest, cost: medium, gated on data)**
`calculate_psr` and `calculate_bootstrap_risk` are built, tested, and **deliberately not wired
into any verdict** (`known-issues.md` §7), parked until a real MT5 export can set the policy
thresholds. Bootstrap risk-of-ruin is the strongest single upgrade available: it turns a bare
`ELIMINAR / score 43` into *"P(hitting your ruin threshold in the next N trades) = 12% [p95 max
DD 31%]"* — and, because it resamples the EA's own per-trade P&L, it uses the **same DD
definition on both sides**, sidestepping the unverifiable `worst_dd_1m`/SQX semantics that block
`known-issues.md` §3.
- *Value:* highest — converts every kill/keep from a point score into a probability with a band.
- *Cost:* the code exists; the real cost is a **policy decision** (what P(ruin) gates what) that
  needs one real dataset to calibrate.
- *Risk:* wiring it *changes live verdicts* — it must not ship silently.
- *Breaks:* the 41-key metrics contract and any test asserting current verdicts; version the
  change and keep the unwired path until thresholds are set.

**2B. Present PSR next to the Sharpe/verdict. (value: high, cost: low-medium, gated on data)**
Same story as 2A, lower effort: PSR already answers "P(true Sharpe > 0 | n, skew, kurtosis)".
Surfacing it *as a displayed statistic* (not yet as a gate) is nearly free and immediately more
honest than the current bare Sharpe. The gating decision waits for data; the *display* does not.

**2D. Multiple comparisons when screening many candidates in incubation. (value: real for
incubation, cost: medium)**
The incubation dashboard screens many candidate strategies at once, each with an independent
`p < 0.03` exact-binomial win-rate gate (`_binomial_p_value`, `incubation_validator.py`) and
**no family-wise or FDR correction**. Note the gate *fails* on a low p-value, so the multiplicity
risk is a **false failure**: screen ~40 candidates and you expect on the order of one healthy
strategy tripped into OBSERVAR/ELIMINAR by chance alone. The theoretically correct fix is
the **Deflated Sharpe Ratio** (it deflates the benchmark by the number of trials) — but P-B found
**no adoptable, properly-licensed Python DSR**, and MinTRL needs `erfinv` that stdlib lacks.
- *Cheapest honest interim:* apply a **Benjamini-Hochberg FDR adjustment** to the binomial
  p-values across the screened set, or — even cheaper — **display the number of candidates
  screened** next to the results so the trader knows the screen is multiplicity-inflated.
- *Value:* real, but only for the *incubation/screening* path. The live single-EA validator has
  no binomial gate at all and asks "does THIS EA still match ITS backtest" — a within-subject
  question where multiplicity doesn't apply.
- *Cost:* medium (hand-roll BH; DSR is a research project).
- *Risk:* changes which candidates pass the gate.
- *Breaks:* the incubation gate outcomes; ship behind the same data-calibration gate as 2A.

**2E. Grounding the hand-tuned weights. (value: medium, cost: low for the honest version)**
The weights are pervasive and, on the record, ungrounded: `validator.CONFIG` (riesgo 35 / edge 30
/ caracter 15 / desv 20, and all the sub-weights) is a **port of a spreadsheet**
(`EA_Validator_Final_v2.xlsx`), and CP3's deviation/risk weights and 100/65/25/0 anchors have no
stated derivation. They *cannot* be fitted without labelled real outcomes (which the project
doesn't have and won't invent).
- *What you can do cheaply and honestly instead of "grounding" them:* a **weight-sensitivity
  check** — perturb the weights ±20% and report whether the verdict flips. A verdict that is
  robust to weight perturbation can be trusted; one that flips is a coin-toss dressed as a score,
  and should be *flagged as such* rather than presented as precise. This is more honest than any
  fake calibration.
- *Value:* medium — it doesn't make the weights "right," it makes the tool honest about when the
  weights are load-bearing.
- *Cost:* low — a loop over perturbed weights on an existing scorer.
- *Risk / breaks:* none; it's an additional read-only signal.

### What NOT to do (Section 2)

- **No regime detection.** It is not in the backlog, needs an exogenous market-state feed the tool
  does not ingest, and is a speculative, high-cost build. The existing "Desviación Estructural"
  flag (≥3 simultaneous deteriorations) is the *right-sized* proxy — it detects the EA drifting
  from its own backtest, which is the actual question. Leave it.
- **Do not put correlation into the kill decision.** The per-EA question is "does this EA still
  match its backtest." Cross-EA correlation is a *portfolio* question the per-EA view structurally
  cannot answer, and forcing it into a single-EA verdict would be a category error. Keep the
  existing `/api/correlation` heatmap as the informational display it already is (see §3).
- **Do not hand-roll DSR/PBO/CSCV speculatively.** Only build them if/when the incubation engine
  actually *selects among many competing candidates* — a different, future conversation.
- **Do not wire PSR/bootstrap into live verdicts before you have real data to set thresholds.**
  That is the project's stated doctrine and it is correct; respect it.

---

## Section 3 — Capability

The bar: **does it change what the trader DOES, or is it decoration?** I kill my own decorative
ideas at the end.

**3A. Ruin probability as a first-class output. (value: highest — this is the capability, and it
overlaps 2A)**
Today the tool cannot tell the trader *"how likely is this EA to blow past my ruin threshold."*
That number changes a keep/kill decision far more than a 79.0 score does. The engine already
computes it (`calculate_bootstrap_risk`); the capability gap is purely that it never reaches the
trader. This is the same build as 2A, seen from the "what does the trader DO differently" angle:
a trader kills an EA at 12% ruin probability that they would have kept at "score 52."

**3B. "How many more trades until this verdict is trustworthy." (value: high, cost: medium)**
For incubation especially, the trader's real decision is often *wait vs act now*. The engine has
checkpoint logic and an exact binomial test; it can compute the **N of additional trades needed
before the win-rate gate reaches significance** at the current observed rate. Surfacing that turns
"OBSERVAR" into "OBSERVAR — needs ~35 more trades to call," which directly changes whether the
trader waits or pulls the plug.
- *Value:* high — acts on the single most common incubation decision.
- *Cost:* medium — a power/sample-size calculation on top of the existing binomial machinery.
- *Risk / breaks:* additive; no verdict changes.

**3C. Divergence *deltas*, not just snapshots. (value: medium, cost: higher)**
The tool is a snapshot: it shows today's state, not *"this EA crossed from MONITOREAR toward
ELIMINAR this week."* A trend/delta would change *when* the trader looks. But it requires
persisting verdict history, which the current runtime-cache model doesn't keep.
- *Value:* medium — changes attention timing, not the verdict itself.
- *Cost:* higher — needs a history store and a diff.
- *Verdict:* worth doing *after* 3A/3B, not before.

### Decorative ideas I am killing explicitly

- **Regime/ML market-state dashboard** — does not change the keep/kill of an EA-vs-its-backtest;
  it decorates. Killed (also killed in §2).
- **Portfolio optimizer / efficient frontier** — a different product (allocation, not keep/watch/
  kill). It would project precision the per-trade data cannot support. Killed.
- **Real-time MT5 streaming** — the decision cadence is weekly; live ticks change nothing the
  trader *does*. Killed.
- **More/prettier charts** — decoration by definition. Killed.

---

## Section 4 — Honesty of the interface

The backend *knows* when it is guessing — it computes a significance tier (`signif`), tags SQN
`"(orientativo)"` and withholds its quality label below n=20, and distinguishes SIN DATOS. The
interface then **strips, buries, or contradicts those hedges**. The literal token `orientativo`
appears in the backend, docs, and tests but has **zero occurrences** in `templates/*.html` and
there is no low-confidence CSS class — where the hedge survives at all it is styled identically to
a solid subtitle, and on several surfaces it is dropped outright.

Ranked by value / cost:

**4A. Fix the display-vs-decision rounding contradiction. (value: highest, cost: near-zero — do
first)**
`_resolve_cp3_verdict` decides on the **raw** score (`if score >= 65`, `incubation_validator.py:830`),
while the engine publishes it pre-rounded (`round(final_score, 2)`, `:1170`) and the view then
formats that value (`incubation_strategy.html`). A raw **64.998** returns **OBSERVAR** but surfaces
as **65.0**, which the eye reads as APROBAR (`known-issues.md` §14-A1; same latent structure in `validator.py` E and the
`live_vs_bt_profit_ratio` 120.04→120→OK case C7). This is not a confidence issue — it is the UI
stating something the engine did not decide.
- *Value:* highest — it removes an outright factual contradiction on the verdict surface.
- *Cost:* near-zero — decide and display off the *same* rounded value, or show the raw score.
- *Risk / breaks:* none of substance; a couple of display assertions.

**4B. Make thin-sample verdicts *look* tentative. (value: high, cost: low-medium)**
A KILL on 14 trades renders identically to a KILL on 500: full-precision score in a fully
saturated ring/pill. The backend already hands the UI `signif` ("Alta/Media/Baja/Muy baja") — but
it sits in a different column and never desaturates the score or verdict. Bind score/verdict
saturation (and displayed precision) to `signif`: low significance → desaturated color, fewer
decimals, an explicit "(muestra chica)" marker on the pill itself.
- *Value:* high — the verdict's confidence becomes visible where the trader actually looks.
- *Cost:* low-medium — reuse an existing backend signal; CSS + one template binding.
- *Risk / breaks:* purely visual.

**4C. Render "(orientativo)" distinctly, on every surface. (value: medium-high, cost: low)**
where SQN's `"(orientativo)"` note appears (the strategy and dashboard KPI cards) it is rendered in
the same `.kpi-sub` style as any solid subtitle, and it is **dropped entirely on the incubation
strategy view and the per-EA table**, which show a green/red SQN number with no hedge at all. Give
it a distinct low-confidence treatment and show it wherever the number shows.
- *Value:* medium-high. *Cost:* low. *Breaks:* nothing.

**4D. Pass thresholds from backend to templates. (value: medium, cost: low-medium — structural
honesty)**
The cut constants (SQN 2.0/1.6, CP gates 5/20/40, CP3 65/45, DD×1.5, p<0.03) are **hardcoded and
duplicated in the templates** (`known-issues.md` §13), matching the engine today by coincidence
with nothing keeping them in sync. Inject them from backend context. This prevents a silent
future lie where the UI colors by a threshold the engine no longer uses.
- *Value:* medium (prevents a whole class of drift). *Cost:* low-medium. *Breaks:* nothing today.

**4E. Add a borderline band at thresholds. (value: medium, cost: low-medium)**
Every gate snaps hard: 69.9 amber / 70.0 green, 64.9 vs 65.0 on opposite sides of APROBAR with no
middle state. A "borderline" rendering within ±ε of each cut tells the trader when a verdict hangs
by a hair. (4A must land first — a borderline band on a lying number is worse than neither.)
- *Value:* medium. *Cost:* low-medium. *Breaks:* nothing.

### What NOT to do (Section 4)

- **Do not fake a confidence interval in the UI before 2A/2B exist.** Until bootstrap/PSR are
  wired, use the ordinal `signif` tier you already compute — do not draw a band you cannot
  compute. A fabricated CI is a worse lie than a bare number.
- **Do not build a full uncertainty-visualization system.** 4A–4C are small, targeted, and remove
  actual dishonesty; a bespoke viz framework is decoration.

---

## Consolidated ranking (value / cost)

*A curated shortlist of the items worth starting with — not every item argued above (it omits the
lower-priority 1B, 3C, and 4E).*

| Rank | Item | Section | Value | Cost | Gated on real data? |
| --- | --- | --- | --- | --- | --- |
| 1 | **4A** display-vs-decision rounding fix | Honesty | highest | near-zero | no |
| 2 | **1A** kill import-time side effects (unlocks route tests) | Structural | high | low | no |
| 3 | **4B** thin-sample verdicts look tentative | Honesty | high | low-med | no |
| 4 | **4C** render "(orientativo)" distinctly everywhere | Honesty | med-high | low | no |
| 5 | **2B / 3A-display** show PSR + ruin probability (display only) | Stats/Cap | high | low-med | display no / gating yes |
| 6 | **3B** "N more trades to a trustworthy verdict" | Capability | high | medium | no |
| 7 | **2E** weight-sensitivity flag | Stats | medium | low | no |
| 8 | **4D** thresholds from backend to templates | Honesty | medium | low-med | no |
| 9 | **2A / 3A-gating** wire bootstrap ruin into verdicts | Stats/Cap | highest | medium | **yes** |
| 10 | **2D** FDR/DSR for multi-EA screening | Stats | med (incubation only) | medium | **yes** |

The cluster that is **high value, low cost, and needs no new data** — 4A, 1A, 4B, 4C, plus the
*display-only* halves of 2B/3A — is where to start. It is mostly honesty work, and it is cheap.
The highest-ceiling items (2A/3A-gating, 2D) are real but blocked on one real MT5 export; the
correct sequence is to build the display-only surfaces now and wire the gates once that dataset
exists.

## What NOT to do — consolidated

1. Re-run the domain decomposition — **done already.**
2. Blueprints / `app/` package / DI — ruled out, no pain point argues for them.
3. Regime detection — not in backlog, needs a feed the tool lacks, speculative.
4. Correlation inside a single-EA kill decision — category error; keep it as a display.
5. Speculative DSR/PBO/CSCV — only if the incubation engine truly selects among many candidates.
6. Wiring PSR/bootstrap into live verdicts before real-data calibration — violates the project's
   own (correct) doctrine.
7. Faking confidence intervals in the UI before the statistics exist.

**One line:** most of the value here is honesty, not new mathematics — and the new mathematics
that would help most is already written and waiting for one real dataset to be turned on safely.
