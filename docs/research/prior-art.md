# P-B — Prior art: how mature OSS quant projects solve what this repo solves

**Deliverable**: report, not code. No production file was touched.
**Method**: two adversarial research rounds (302 agents, ~10.8M tokens, ~2,800 tool calls), then two blind judges on the report itself, then two targeted digs to repair what they broke. Every load-bearing claim about *code* was fetched from a primary source (raw source files, PyPI JSON API, GitHub REST API) and attacked by three independent skeptics; a claim needed 2-of-3 refutations to die. 25 claims survived round 1 (7 killed), 60 survived round 2 (4 killed).
**Two claims about *published definitions* (Van Tharp's SQN, Pardo's WFE) could NOT be verified against primary sources** — both authors' canonical texts are in paywalled books, and vantharpinstitute.com hard-403s. Those two are labeled inline and are the weakest material here. §2.2 and §4.5.
**All liveness data fetched 2026-07-17.** Code-behavior claims are stable; maintenance claims decay fast. Re-verify before acting on any dependency decision.

> This document is in English because the brief and every cited source are in English. The rest of `docs/` follows the repo's Spanish convention.

---

## 0. The headline, before anything else

**The ecosystem does not solve our problem, and the reason is structural, not accidental.**

The entire `empyrical` → `pyfolio` → `quantstats` lineage is built on **time-indexed period return series**. This repo analyzes **discrete per-trade records**. That is not a small impedance mismatch you paper over with an adapter — it is a different data shape with a different semantics, and the libraries corrupt the metric the moment you feed them trades.

QuantStats says so itself, in a dedicated README section titled *"Important: Period-Based vs Trade-Based Metrics"*:

> "QuantStats analyzes return series (daily, weekly, monthly returns), not discrete trade data." … "A single 5-day trade might span 3 positive days and 2 negative days — QuantStats would count these as 3 wins and 2 losses at the daily level."

That is precisely the corruption an MT5 per-trade analyzer exists to avoid, and it hits Win Rate, Profit Factor, payoff and consecutive-loss counts alike.

So the answer to "should we adopt the famous library?" is not *"it would be overkill."* It is **"it would be wrong."** Our hand-rolled per-trade implementation is not the naive option we settled for. Given our data shape, **it is the only semantically correct option the survey found**, and the survey's job turned out to be confirming that rather than replacing it.

That said, the survey found five things genuinely worth taking, two entries in our own docs that assert more than the evidence supports, and one feature whose *name* promises something it cannot deliver. Those are the payload.

---

## 1. Maintenance audit — verify liveness before you trust a name

The brief's warning was justified, but the trap is subtler than "famous project is dead."

**GitHub's `archived` flag returns FALSE for `empyrical`, `pyfolio` and `zipline`.** All three are abandoned. An automated liveness check keyed on that flag produces a **false negative across the entire quantopian family**. Never use `archived` alone as the gate — cross-check PyPI upload date AND GitHub `pushed_at` AND actual commit cadence, and beware that `pushed_at` moves on tag pushes and branch deletions with zero commits behind it.

Fetched from the PyPI JSON API on 2026-07-17:

| Package | Latest | Released | Verdict |
|---|---|---|---|
| `empyrical` | 0.5.5 | 2020-10-13 | **Dead** — 5.8y. Predates NumPy 2 entirely |
| `pyfolio` | 0.9.2 | 2019-04-15 | **Dead** — 7.3y |
| `zipline` | 1.4.1 | 2020-10-05 | **Dead** |
| `backtrader` | 1.9.78.123 | 2023-04-19 | **Dormant** — 3.2y cold; *no branch* carries a later commit |
| `empyrical-reloaded` | 0.5.12 | 2025-06-01 | **Usable, not active** — 13.5mo; last commit a README typo |
| `pyfolio-reloaded` | 0.9.9 | 2025-06-02 | **Dormant** — 13mo, zero default-branch commits |
| `quantstats` | 0.0.81 | 2026-01-13 | **Stalled** — all human signals collapse to one date; later pushes are Dependabot on a side branch |
| `arch` | 8.0.0 | 2025-10-21 | **Maintenance mode**, bus factor 1 (113/135 commits by one person) |
| `backtesting.py` | 0.6.5 | 2025-07-30 | **Low-activity but alive** — 11 commits/12mo |
| `ffn` / `bt` | 1.1.5 / 1.2.0 | 2026-03-24 / 2026-04-25 | **Alive** — contradicts the common assumption |
| `skfolio` | 0.20.1 | 2026-04-21 | **Alive** |
| `riskfolio-lib` | 7.3.0 | 2026-05-31 | **Alive** |
| `vectorbt` | 1.1.0 | 2026-07-05 | **Actively maintained** |
| `nautilus_trader` | 1.230.0 | 2026-06-29 | **Actively maintained** |

Two corrections to widely-repeated folklore, both verified:

- **`vectorbt` (free, `polakowo/vectorbt`) is NOT dead in favor of `vectorbt.pro`.** Version 1.1.0 shipped 2026-07-05; master carries real numerical work through 2026-07-14 (Welford's algorithm for rolling std, Python 3.14/pandas 3 support) and merges outside PRs. PyPI and the open repo are one lineage.
- **`empyrical-reloaded` — which we already depend on in `requirements-dev.txt` — is "usable", not "actively maintained".** An initial claim that it was actively maintained was **refuted 0-3**. It works on a modern stack (no `numpy<2` cap; Python 3.13 classifiers), but its last release is 13.5 months old, it is `Development Status :: 4 - Beta`, its scope is compatibility-only, and it pins `peewee<3.17.4` — an upper cap that is its own dependency risk. **Verdict: fine where it sits (a test-only differential oracle), but do not promote it to production and do not build anything new on it.**

---

## 2. Metric conventions — where we already agree, and where we don't

### 2.1 Drawdown denominator — we are already correct ✅

Universal, three independent primary confirmations. Drawdown is measured against the **running peak (high-water mark)**, never against initial capital:

- **empyrical**: `max_return = np.fmax.accumulate(cumulative)`; then `nanmin((cumulative - max_return) / max_return)`
- **QuantStats**: `(prices / prices.expanding().max()).min() - 1`
- **vectorbt**: `dd_drawdown_nb → (valley_val - peak_val) / peak_val`

**Our `metrics.py:194-195` computes `peak_abs = capital + peak_pnl` and divides by it.** Since our equity curve is `pnl` accumulated from 0, absolute equity *is* `capital + pnl`, so `capital + peak_pnl` **is the running peak of absolute equity**, and we match the universal convention. Our inline comment at `:189-193` — that `max_dd_pct` must be tracked independently because `peak_abs` moves between points — is a real subtlety the libraries handle implicitly by working in normalized-equity space.

**Two caveats, both raised by a judge against an earlier draft that claimed the identity held "exactly" with "no change needed".** The algebraic identity holds only **for `capital > 0`, on the filtered curve**:

1. **`capital <= 0` diverges.** The guards at `metrics.py:158` and `:195` (`if peak_abs > 0 else 0.0`) **silently force DD% to 0.0**, masking real drawdown. The library convention has no such branch, because normalized-equity peaks are always positive. **Our own ledger already documents this** (`known-issues.md:243-244`: *"`capital <= 0` hace `peak_abs <= 0` y el DD% cae silenciosamente a 0.0, enmascarando el drawdown real. La config no valida capital no positivo."*).
2. **Untimed trades are excluded from the curve** (`metrics.py:126-130`, `:550-556`), so the curve is not the account's absolute equity when they exist — while their P&L still counts toward `net_profit`, SQN and Sharpe. Deliberate and documented, but it means "absolute equity" is an approximation.

So: **the convention is right and needs no change; the guard behavior at `capital <= 0` is a known open defect, not something this survey clears.**

One trap worth knowing if we ever differential-test against QuantStats: **it prepends a phantom baseline row** (`1e5` if first price > 1000, `100.0` if > 10, else `1.0`) before the expanding max, so its initial value floors the running peak. empyrical does not. **The two disagree at the first bar despite sharing the convention**, and QuantStats' baseline is a *magnitude heuristic*, not the real account balance — on an equity curve starting near a threshold (1000.5 vs 999.5) it picks different baselines. Our `capital` is the real number; we are on firmer ground than QuantStats here.

### 2.2 SQN — the cap is an unverified attribution, and there is a better-supported divergence we've missed ⚠️

**Status: §5.5's recommendation was applied on branch `pb-followup`.** `docs/known-issues.md` §7 no longer asserts *"divergencia … contra el estándar"* — the entry now states the Tharp attribution is unverified, and the R-multiple-vs-raw-P&L divergence below has been added as its own entry. The xfail's `reason` string was updated accordingly (see `tests/oracle/test_diff_metrics.py:662-691`). This subsection's analysis is the research trail that motivated that fix; kept as-is below.

**Epistemic warning up front, because an earlier draft of this report got this wrong.** It claimed our `known-issues.md` was "factually wrong" about the Tharp cap. **A blind judge refuted that as absence-of-evidence dressed as evidence-of-absence, and the judge was right.** What follows is the corrected, weaker, defensible version. This is the softest material in the report; treat it accordingly.

**Finding 1 — every SQN implementation found is uncapped. Three for three**, verified verbatim:
- `backtrader/analyzers/sqn.py`: `sqn = math.sqrt(len(self.pnl)) * pnl_av / pnl_stddev`
- `backtesting.py/_stats.py`: `np.sqrt(n_trades) * pl.mean() / (pl.std() or np.nan)`
- `vectorbt/portfolio/trades.py`: `np.sqrt(count) * pnl_mean / pnl_std`

No `min`, `clip` or `clamp` on any SQN path in any of them. QuantStats has no SQN at all (consistent — SQN needs a trade list). **Scope note: this is three implementations, not "the ecosystem."** It is real evidence of common practice and nothing more.

**Finding 2 — the cap's attribution to Tharp could NOT be verified, in either direction.** A dedicated dig failed to reach any primary source: **`vantharpinstitute.com` returns HTTP 403 on every URL**, `vantharp.com` 301s into the same wall, archive.org was unavailable, and *Definitive Guide to Position Sizing* (2008) — the book where SQN was introduced and where the alleged cap allegedly lives — **is paywalled with no quotable excerpt**. The book is precisely what we cannot read.

The cap traces to exactly **two hedged third-party paraphrases**, neither quoting Tharp, neither citing a page: IndexTrader (*"as a work around he suggests traders use 'N=100' for when there are more then 100 trades"*) and a nexusfi forum thread (*"one way he suggests to cope"*). **Even at face value these describe an informal remedy for large samples, not a term of the published formula.** Counter-evidence: Jonathan Kinlay derives SQN as `(Expectancy / stdev of R) * sqrt(N)` with no cap; a Wealth-Lab user implements `Math.Min(10, ...)` and explicitly describes it **in his own voice as his own modification**.

A likely origin of the confusion: **Tharp's own *Market SQN*** applies the formula to daily price change over a **fixed 100-day window** — a normalization basis, where `sqrt(100)=10` falls out as a constant, not a clamp. "Market SQN always uses 100" mutating into "SQN caps N at 100" is an easy slip.

**So the honest verdict is: `known-issues.md` is not proven wrong — it is UNVERIFIED, and it asserts an attribution that freely available primary sources do not support.** Whether Tharp informally suggested N=100 somewhere in the book is **genuinely unknown and should not be resolved in either direction.**

**Finding 3 — the divergence from Tharp that IS well-supported, and that we've missed entirely.** Tharp Institute's own definitional wording (recovered via search snippets of the 403'd page, reproduced and attributed on TradingView):

> "SQN measures the relationship between the mean (expectancy) and the standard deviation of **the R-multiple distribution** generated by a trading system. It also makes an adjustment for the number of trades involved."

**SQN is defined over R-multiples — returns normalized by the initial risk per trade — not raw currency P&L.** The dig rated this **high confidence** — the strongest claim in this subsection — corroborated by the independent sources it could reach (Kinlay's derivation, and the Tharp Institute glossary text itself). ⚠️ Note the same limit applies as to the cap: **the primary source is the same 403'd site and the same paywalled book.** This is better-evidenced than the cap, not independently confirmed. Note the wording also says *"an adjustment for the number of trades"* with **no ceiling stated**.

**Our `_calc_sqn` (`metrics.py:394-425`) feeds it raw `net_pnl`.** So do backtrader, backtesting.py and vectorbt. **That is a real divergence from Tharp's definition, it is far better evidenced than the cap, and neither our docs nor our test ledger mentions it.** It also matters more: R-multiples make SQN comparable across position sizes; raw P&L does not, so an EA that varies lot size has an SQN partly measuring its sizing rather than its edge.

**Recommended action** (report-only; not applied):
- **Amend, do not delete**, `known-issues.md:210-224` (already applied — see status note at the top of this subsection). Replace *"la divergencia es contra el estándar"* with the accurate epistemic state: *the cap at N=100 is not part of Tharp's SQN formula as stated in his accessible material and appears to be a community convention; the primary source (his book) is paywalled and this could not be settled.* **The ledger's contract is "todo lo de acá está probado" — an unverified attribution violates that contract and is exactly why this needs fixing.**
- **Add the R-multiple divergence** as a new, better-supported entry. This is the finding worth having.
- **Do not rename the test to `test_sqn_uncapped_grows_unbounded_with_n`** — a judge caught that this would be *at least as inaccurate*: the test runs a **single fixed N=150 sample** and asserts nothing about growth with N. If renamed at all, something like `test_sqn_uncapped_diverges_from_community_capped_convention_at_n150` is accurate. The cleanest fix may be to leave the test alone and correct its `reason` string, which is where "el estandar de Tharp" actually appears (`:646-654`).
- **The underlying concern remains legitimate and is ours to decide**: at N=2500, `mean/std = 0.108` gives SQN 5.42 "Sobresaliente" vs 1.08 capped. `MIN_TRADES_FOR_SQN_LABEL = 20` guards the small-N end; nothing guards the large-N end. **That is a real open decision** — just not, on this evidence, a deviation from a documented standard.

**Two conventions to pin if we ever differential-test SQN against backtrader** (we currently use a hand-written stdlib oracle, which is fine):
- backtrader uses **population stdev** (`mathsupport.standarddev(..., bessel=False)` → divides by N). We use `ddof=1`. **backtrader reports higher by exactly `sqrt(N/(N-1))`** — ~5.4% at N=10, ~1.7% at N=30. backtesting.py and vectorbt use `ddof=1` like us, so **we match the majority**.
- backtrader appends `trade.pnlcomm` — **net of commission**. Our `net_pnl = profit + commission + swap` is the same intent. Good.

### 2.3 Sharpe on per-trade data — nobody has a principled answer, and ours is more honest than most

**Verified: no surveyed library has a principled basis for Sharpe on irregular per-trade data.** Both empyrical and QuantStats annualize by `sqrt` of a **declared period constant** and operate on **array position, never elapsed time**:

- **empyrical**: `sharpe_ratio` multiplies by `np.sqrt(ann_factor)` where `ann_factor = annualization_factor(period, annualization)` is a **pure dict lookup** (`daily:252, weekly:52, …`). Its only `ValueError` fires on an unrecognized period *name* — **never on data spacing**. No `DatetimeIndex` is ever inspected. Reductions are `axis=0`. Verified byte-equivalent in `empyrical-reloaded`, so this is not stale code.
- **QuantStats**: `res = returns.mean() / returns.std(ddof=1)` then `res * sqrt(periods)`. `_prepare_returns` does **no resample, no `infer_freq`, no elapsed-time measurement**. Corroborating tell: it computes `years = len(returns)/periods` — **observation-count-based, not calendar-based**.

**Feed either one N per-trade P&L values and it multiplies by `sqrt(252)` whether those trades span one week or ten years, silently, with no warning.** And `sqrt`-time scaling is only valid under iid returns at the declared frequency; serial correlation can overstate annualized Sharpe by >65% (arXiv 1905.08042).

There is also a **live trap**: QuantStats' `_prepare_returns` sniffs prices-vs-returns via `elif data.min() >= 0 and data.max() > 1: data = data.pct_change()`. **An all-non-negative per-trade P&L series containing any value > 1 gets silently `pct_change`'d as if it were a price series.**

**Our `_calc_sharpe` (`metrics.py:379-391`) does `mean / std(ddof=1)` and stops — no annualization.** (Its docstring, *"Simplified per-trade Sharpe = mean(R) / std(R, ddof=1). No risk-free rate"*, states the formula and the missing risk-free rate but does not explicitly say annualization is omitted — worth a word, since that is the more consequential omission.) Against a library that would blindly multiply by 15.87, **not annualizing is the more defensible engineering choice** — we return a number whose stated meaning matches what we computed.

**The one library that does this correctly is `nautilus_trader`**, and its approach is the prior art worth studying (§5.3).

### 2.4 The one genuinely non-obvious idea: vectorbt's drawdown *record* model

vectorbt models drawdowns not as a rolling series minimum but as **discrete episodes** — `class Drawdowns(Ranges)` with a structured dtype: `peak_idx, start_idx, valley_idx, end_idx, peak_val, valley_val, end_val, status`. The load-bearing mechanic is the **per-episode peak reset** in `get_drawdowns_nb`: after writing each record it executes `peak_idx = i; valley_idx = i; peak_val = cur_val; valley_val = cur_val`.

And it carries a formal in-repo warning that reads as **anti-marketing** — a library flagging a footgun in its own defaults:

> "`Drawdowns` return both recovered AND active drawdowns, which may skew your performance results. To only consider recovered drawdowns, you should explicitly query `recovered` attribute."

The **Active vs Recovered** distinction bites hardest on *average* drawdown (an unrecovered episode that may yet recover biases the mean), less so on *max* drawdown. Our `stagnation_days` is a partial, weaker expression of the same idea: it knows we are underwater but not that "underwater" is an **episode with identity**.

This is the one concept worth borrowing — see §5.4.

---

## 3. Monte Carlo — the ecosystem's implementations are actively defective

This matters because **we have no Monte Carlo at all**: `_calc_risk_of_ruin` was deleted (pinned by `tests/test_metrics.py:397-401`), and "Monte Carlo" in this repo means numbers typed in by the operator from StrategyQuant X.

**The good news: the libraries offer nothing to adopt, because what they have is broken.**

### 3.1 QuantStats' Monte Carlo is degenerate

It lives in `quantstats/_montecarlo.py` and uses **permutation, not bootstrap**:

```python
rng = np.random.default_rng(seed)
for i in range(1, sims):
    sim_returns[:, i] = rng.permutation(returns_array)
```

No `replace=True`. Then `cumulative = np.cumprod(1 + sim_returns, axis=0) - 1`, and `goal_probability` compares terminal values.

**Permutation preserves `prod(1+r)`, so the terminal value is IDENTICAL across every simulation.** `goal_probability` is therefore mathematically pinned to exactly **0.0 or 1.0** — never anything between. `montecarlo_cagr` is degenerate for the same reason.

The library's own `docs/montecarlo.md` **admits the mechanism**:

> "Because shuffling preserves the product of all (1+r) values, the terminal value is the same across all simulations."

…and then, in the same file, prints `"Probability of reaching goal: 67.8%"` and `{'min': -0.15, 'max': 0.85, 'std': 0.18}` — **all impossible when terminal values are identical** (std must be 0.0; min must equal max). **The docs are not reproducible from the implementation.**

*Mechanism note, corrected during verification*: permutation does not understate tails by "failing to generate long loss runs" — it actually clusters scattered losses into runs *longer* than realized, which is why max-DD varies at all. The real reason it understates tails is that it **fixes the empirical multiset**: each return is used exactly once, so nothing worse than the realized worst can ever appear. `rng.choice(..., replace=True)` can redraw the worst loss repeatedly, producing the fat tails that matter for ruin.

### 3.2 QuantStats' `risk_of_ruin` is catastrophically wrong

Full body:

```python
def risk_of_ruin(returns, prepare_returns=True):
    if prepare_returns:
        returns = _utils._prepare_returns(returns)
    wins = win_rate(returns)
    # gambler's ruin formula
    return ((1 - wins) / (1 + wins)) ** len(returns)
```

The only inputs are win rate and sample length. **No position sizing, no payoff ratio, no capital, no ruin threshold.** `[+0.001, -0.99]` and `[+0.99, -0.001]` return **identical** output.

Arithmetic independently reproduced: at win_rate 0.55, base = `0.45/1.55` = 0.29032; `0.29032^100` = **1.94e-54**. At N=1000 it literally underflows to `0.0`.

And it is **worse than "wrong tolerance"** — it is a **category error**. Textbook gambler's ruin is `(q/p)^U` where `U` is **capital units before ruin**. QuantStats exponentiates by **sample length**. So `(q/p)^100 = 1.93e-9` vs QuantStats' `1.94e-54` — a **~45-order-of-magnitude divergence from the formula its own comment names.** Conflating "periods observed" with "units of capital at risk" means **ROR falls as you collect more data**, which is backwards.

Issue #298 ("Risk Of Ruin Formula") makes the same structural point independently. **It is closed, and the formula is unchanged on main** — the defect is current.

### 3.3 What to adopt: the seeding model only

**`np.random.default_rng(seed)` is the modern `Generator` API and is the correct model** — QuantStats gets this one thing right. Do not use legacy `np.random.RandomState`. `arch` confirms the convention: `seed: int | Generator | RandomState | None = None`, where an int routes to `default_rng` (PCG64), and there is **no `random_state` parameter anywhere** in its `base.py`.

**Everything else: hand-roll.** See §5.1.

---

## 4. Strategy validation — is there prior art better than our hand-weighted 0-100?

This was the brief's highest-value question, aimed squarely at `validator.py`, whose weights are transcribed from `EA_Validator_Final_v2.xlsx` (`validator.py:17`) with **no statistical justification anywhere in the repo** and **no statistical test comparing live vs backtest distributions** — only point-estimate deltas against hardcoded tolerance bands.

Round 1 produced **zero verified findings** here. Round 2 was built specifically to close that, and the answer is now clear and mostly negative.

### 4.1 The Bailey & López de Prado family: essentially unavailable

| Where | Status |
|---|---|
| **`mlfinlab`** | **Gutted.** Every function in `backtest_statistics/statistics.py` — `probabilistic_sharpe_ratio`, `deflated_sharpe_ratio`, `minimum_track_record_length` — has a full docstring and a body of **`pass`**. Repo-wide: **429 `def` vs 420 bare `pass`.** **Not installable**: PyPI serves zero files (`{"files":[],"versions":[]}`); `pip download mlfinlab` → "no matching distribution". License is a **proprietary Hudson & Thames agreement**, not open source. |
| **`arbitragelab`** | Genuinely BSD-3 and genuinely real code (735 defs / 2 passes) — but implements **none** of the family. Dormant (~2y). ⚠️ Contains `sparse_eigen_deflate()`, which is **linear-algebra eigenvector deflation, not Deflated Sharpe** — a false-positive trap. |
| **`vectorbt`** | **Has DSR** (`deflated_sharpe_ratio` in `returns/metrics.py`) + `approx_exp_max_sharpe`. Actively maintained. **But: Apache-2.0 + Commons Clause — NOT OSI open source; forbids selling.** No PSR, no PBO, no MinTRL. |
| **`quantstats`** | Has PSR, **with real bugs** (§4.2). No DSR, no PBO, no MinTRL, no Reality Check. |
| **`deflated-sharpe`** (standalone) | Apache-2.0, but a **hobby project built in one ~30-minute sitting** (14 commits, 13:24–13:54 on 2026-03-21) and untouched since. Deviates from the paper in 3 ways and **returns a z-score rather than a probability**. Its advertised "paper verification" is **circular** — the test recomputes the library's own formula inline and asserts self-agreement. |
| **López de Prado's own code** | `quantresearch.org/Software.htm` — loose **`.py.txt` research-appendix scripts** (DSR 2014, CSCV 2013, PSR 2012), nothing newer than 2015. **Licensing hazard**: a non-standard GPL, "non-business purposes only," authors retaining commercial rights requiring written pre-authorization — **additional restrictions GPL §7 disallows.** And they aren't APIs: `DSR.py.txt` is a Monte-Carlo validation script that writes a CSV; there is **no `deflated_sharpe_ratio()` function in it**. |
| **skfolio, riskfolio-lib, ffn, bt, pyfolio-reloaded, backtesting.py, nautilus_trader** | **None implement any of the five**, verified at HEAD by keyword search plus complete enumeration of skfolio's `measures` module and backtesting.py's `compute_stats` output. |

**Conclusion: there is no maintained, properly-licensed Python implementation of PSR/DSR/PBO to adopt.** The pattern that held for metrics holds here too — **hand-roll from the published formulas.**

### 4.2 QuantStats' PSR — a useful cautionary tale

It implements PSR and gets the *shape* right, but:
- `stats.py:1188` codes `((kurtosis_no - 3)/4) * base**2` while `stats.py:1615` returns pandas `.kurtosis()`, which is **already Fisher/excess** — so it subtracts 3 twice. The variance term is understated by exactly `0.75*SR²`. *In practice this is ~1e-4 on realistic data* because the function uses non-annualized per-period Sharpe — so this is the **least** damaging defect.
- **`rf` (an annualized rate) is subtracted directly from a per-period Sharpe** in the benchmark-SR slot. `rf=0.05` moves PSR from 0.994 → 0.720. **This one actually bites.**
- **`annualize=True` multiplies a probability by `sqrt(252)`, returning 15.78.** A probability cannot be annualized by sqrt-time scaling — that rule applies to the Sharpe ratio, not to its confidence level.

**Do not call `probabilistic_sharpe_ratio(annualize=True)`. Ever.** The lesson for us is §6.

### 4.3 `arch` — the one real find, but aimed at a different question

**`arch` genuinely implements Hansen's SPA**, verified at v8.0.0 (commit `038d78b`):

```python
class SPA(MultipleComparison, metaclass=DocStringInheritor):
    """Test of Superior Predictive Ability (SPA) of White and Hansen.
    The SPA is also known as the Reality Check or Bootstrap Data Snooper.
    """
    def __init__(self, benchmark, models, block_size=None, reps=1000,
                 bootstrap="stationary", studentize=True, nested=False, *, seed=None):
```

Also ships `StepM` (Romano-Wolf), `MCS` (Model Confidence Set), and the bootstraps: `IIDBootstrap`, `StationaryBootstrap`, `CircularBlockBootstrap`, `MovingBlockBootstrap`, plus `optimal_block_length` (Politis-White). `.pvalues` returns lower/consistent/upper.

**Gotcha worth knowing**: `RealityCheck` is **not a distinct implementation** — it is `class RealityCheck(SPA): pass`, commented `# Shallow clone of SPA`. Runtime-verified to return byte-identical p-values. The docs are honest about this (the `.rst` says SPA is "an improved version of the Reality Check"), though the class docstring is looser. **Anyone needing literal White (2000) RC must verify the mapping themselves** — no source maps `.pvalues['upper']` to RC, and `arch` even contradicts itself internally (docstring line 537 says "Upper: Never recenter"; code comment line 666 says "Upper always re-centers").

**License**: SPDX **`NCSA`** — permissive, MIT-style. (GitHub reports `NOASSERTION`, but that is a text-matching failure, not ambiguity.)
**Dependency weight**: **~71 MB across 10 packages.** It pulls `scipy` (37.3 MB), `pandas`, `numpy`, **and `statsmodels`**. `arch` itself is only 0.93 MB. Requires Python >=3.10.
**Maintenance**: 8.0.0 is current, not archived, but **maintenance-mode with bus factor 1** (bashtage: 113 of 135 commits; the rest mostly bots).

**But here is the honest assessment, and it is the important part: SPA answers a different question than the one `validator.py` asks.**

SPA/StepM/MCS are **data-snooping controls**: given N candidate strategies and a benchmark, is the *best* one genuinely superior, or did you just look at N things? That is a **multiple-testing-across-candidates** problem.

Our `validator.py` asks: **is THIS live EA still behaving like ITS OWN backtest?** One strategy, two samples, a degradation question. **SPA is not the tool for that**, and reaching for it because it is rigorous and available would be cargo-culting rigor.

**Where SPA *could* legitimately apply is the incubation engine**, if and when we are selecting among many candidate EAs and want to control for having looked at many. That is a real, future, different conversation — and today's incubation engine already has the **only genuinely appropriate statistical test in the repo**: the exact binomial gate (`incubation_validator.py:252-268`), pure `math.comb`, no scipy, no normal approximation. **That is good work and it should be said plainly.**

### 4.4 So what actually replaces the hand-weighted 0-100?

**Nothing off the shelf.** The honest finding is that our score's real problem is **not that a library does it better** — it's that **the weights came from a spreadsheet and nobody can say why `w_riesgo=35` rather than 30.** No library fixes that; only a decision record or a calibration against real data does. `known-issues.md` §1 already documents that we are **blocked on data** for exactly this kind of calibration, and it is right to say so rather than tune against invented fixtures.

The realistically adoptable improvement is **PSR hand-rolled from the paper** (§5.2) — it doesn't replace the score, but it puts a defensible confidence statement next to it.

### 4.5 Walk-forward — and the fact that `validator.py` ships a "WFE" that isn't one ⚠️

**Status: the Recommended action below (rename + legend fix) was applied on branch `pb-followup`** — see `known-issues.md` §7 (RESUELTO entry) and §5.6. `validator.py` no longer has a field named `wfe`; it is `live_vs_bt_profit_ratio`, `docs/metrics-formulas.md` §16 is titled "Realización BT %", and the `templates/validator.html` info card lists the four real bands instead of the invented 50% one. The analysis below is the research finding that motivated the fix and is otherwise unchanged; code excerpts referencing the old `wfe` name describe the pre-fix state.

**This section exists because a blind judge caught that an earlier draft never used the word "walk-forward" at all** — despite the brief asking for it by name, and despite `validator.py` having a feature literally called Walk-Forward Efficiency. That was the report's largest miss.

**The standard definition** (⚠️ **secondary sources only** — Pardo's *The Evaluation and Optimization of Trading Strategies* (2008) and *Design, Testing and Optimization of Trading Systems* (1992) are both paywalled/print-only and could not be read):

- **WFE = annualized out-of-sample return / annualized in-sample return**, computed **per rolling walk-forward window** and then aggregated. The annualized-rate normalization exists precisely so long IS windows and short OOS windows are comparable. *(Best attributable statement: TradeStation's Walk-Forward Optimizer help — the TS WFO was built with Pardo.)*
- **The pass threshold usually quoted is WFE ≥ 50%.** ⚠️ **Repeated everywhere, verified nowhere in Pardo's own words** — and notably, Wikipedia's entry, the one source that *does* cite Pardo, carries **no threshold at all**. Treat "Pardo says 50%" as received wisdom.
- **Walk-forward analysis inherently requires a SEQUENCE of optimize-then-test windows, re-optimizing at each step.** Every source agrees. **A single train/test split is not walk-forward analysis — it is holdout validation.** This is the definitional core, and it is the point on which our metric turns.

**What `validator.py:654-683` actually computes** (shown with the field's pre-rename name `wfe` for continuity with the discussion below; the shipped field is now `live_vs_bt_profit_ratio`, see status note above):

```python
bt_profit_per_month   = bt_expect * bt_trades / bt_months
live_months           = weeks_live / 4.33
live_profit_per_month = expect_live * trades_live / live_months
wfe = round((live_profit_per_month / bt_profit_per_month) * 100, 1)  # now live_vs_bt_profit_ratio
# >120 ALERTA | [70,120] OK | [30,70) ALERTA | <30 FUERA
```

**This is not Walk-Forward Efficiency. It is a live-vs-backtest profit-rate ratio wearing the WFE name.** The differences are load-bearing, not pedantic:

- **The numerator is live trading, not out-of-sample backtest.** WFE compares OOS backtest to IS backtest — both simulated, same engine, same data, differing *only* in whether the parameters saw the data. **That isolates one thing: does the optimization generalize.** Ours compares **real live results to a backtest**, so the ratio absorbs slippage, spread, commission, fill quality and regime change *on top of* any overfit — and **cannot separate them.** WFE isolates one variable; this isolates none.
- **The denominator is the whole backtest**, a single aggregate. There is no IS/OOS partition anywhere.
- **No windows, so no aggregation.** One number over one period pair.
- **`>120 → ALERTA` has no analog in the literature.** WFE > 100% just means OOS beat IS. The band is defensible for *live-vs-BT* (beating your own backtest live is suspicious) — which is itself a tell that this is not the literature's metric.

**Repo-wide, there is no parameter optimization of any kind**, so nothing here can be walk-forward analysis in the standard sense. (`metrics.py:465` `_calc_rolling_metrics` is a rolling window over *closed live trades* recomputing descriptive stats — it re-computes, it does not re-optimize.)

**And it structurally cannot be.** We consume an operator-typed **aggregate** (`bt_expect`, `bt_trades`, `bt_months`) — no backtest equity curve, no trade-level backtest history, no parameter space. **A true per-window WFE is not implementable with the inputs this system has.** That constraint is decisive, and it is why this is not a shortcut.

**Verdict — sorted deliberately:**
- **Not wrong.** The arithmetic is correct and internally consistent. Rate-normalizing both sides before ratioing is the right instinct — *the same instinct WFE's annualization encodes*. `docs/metrics-formulas.md:330-334` documents the real computation faithfully.
- **A reasonable approximation — arguably the only one available.** It answers a genuinely useful and *different* question: "how much of the promised backtest edge is actually showing up live?" For a live-EA monitor that is plausibly the *more* operationally relevant question. Keeping it **informational-only** — never scored, never triggering SIN DATOS (`validator.py:508-509`, pinned by `tests/oracle/test_char_validator.py:461-498`) — is exactly the right conservatism for a number this noisy. **That design judgment is sound; leave it alone.**
- **Misnamed — and this is the one thing worth acting on.** To a reader who knows Pardo, "WFE" imports a specific promise: *your optimization generalizes out-of-sample*. This number cannot speak to that. Someone seeing `WFE: 85%` could conclude the strategy survived walk-forward validation when **no walk-forward validation was ever performed.**

**Recommended action** (report-only): rename to `live_vs_bt_profit_ratio` (display: *"Realización BT %"* — the existing tooltip at `templates/validator.html:84`, *"qué % del rendimiento BT se replica en live"*, **is already an honest description of the real computation**; only the three-letter label overclaims). Zero behavior change; removes the overclaim.

**What `known-issues.md` had at the time of this report, and what it missed (state as found; see the status note above for what changed since):**
- ✅ **C7** (`validator.py:681-684`): WFE rounds *before* banding, so 120.04 → 120.0 → OK instead of ALERTA. Verified, and deliberately pinned (`test_char_validator.py:481-498`). Cosmetic — a ±0.05pp sliver on an informational field. Still open; not part of the naming/legend fix.
- ✅ **The phantom "50%" band**: `templates/validator.html` rendered *"≥70% Excelente · 50-70% Aceptable · 30-50% Degradación · <30% Posible overfitting"*. The code had **no 50% boundary** (30-70 was one undifferentiated ALERTA) and the card **omitted `>120 → ALERTA` entirely**. Worse than "incomplete": labelling 50-70% *"Aceptable"* **inverted the signal** — the code raised ALERTA there. **Now fixed** — `known-issues.md` §7 records this as RESUELTO and the card lists the four real bands.
- ❌→✅ **The naming problem was nowhere in the ledger**, and `docs/metrics-formulas.md:328` titled it "Walk-Forward Efficiency (WFE)" unqualified. **Now fixed**: the ledger's RESUELTO entry covers the rename, and `metrics-formulas.md:328` reads *"## 16. Realización BT % (`live_vs_bt_profit_ratio`)"* with an explicit naming note.
- ❌→✅ **`metrics-formulas.md:338-344` split 50-70 and 30-50 into separate rows** that both said ALERTA. Not wrong in outcome, but **this vestigial 50% boundary was very likely where the template's invented "50%" came from.** **Now fixed** — the row was removed; `metrics-formulas.md` §16 has a single 30-70% ALERTA row.
- ❌ **Month-unit mismatch**: `live_months = weeks_live / 4.33` ≈ 30.31 days/month vs `bt_months` as operator-supplied calendar months (~30.44) — ~0.4% systematic bias. **Immaterial** next to the metric's noise floor; noted for completeness, not action.

**Library support**: `skfolio` ships `model_selection.WalkForward` (sklearn-style splitter with purging) and `vectorbt` ships `rolling_split()` / `Splitter` / `@vbt.cv_split()` plus an official walk-forward notebook. **Both are actively maintained. But neither — nor anything else found — ships a WFE metric**; they ship the *splitters* and leave the ratio to you. There is no maintained standalone WFE package. **Irrelevant to us regardless: splitters need a parameter space to re-optimize, and we have none.**

---

## 5. Recommendations

Ordered by value/cost. **None of these were applied — this is a report.**

### 5.1 Hand-roll a Monte Carlo bootstrap for risk of ruin ⭐ highest value

- **What**: iid bootstrap over our per-trade `net_pnl` array — `rng = np.random.default_rng(seed)`, `rng.choice(pnl, size=n, replace=True)` per path, accumulate, report the **fraction of paths breaching a drawdown/ruin threshold**, plus percentile bands on max-DD.
- **Why it matters here**: we deleted `_calc_risk_of_ruin` and have nothing. Meanwhile our DD gate leans on **operator-typed StrategyQuant numbers whose semantics we cannot verify** — `known-issues.md` §3 already admits the 1.5× ALERTA multiplier is *"NO VERIFICABLE"* because we can't confirm SQX defines "max DD %" the way we do. **A bootstrap over our own trades removes that dependency entirely**: same definition on both sides, by construction.
- **Cost**: **~5-10 lines.** numpy only — already a production dependency.
- **License / dep risk**: **none.** Nothing added.
- **Do NOT copy**: QuantStats' permutation (degenerate) or its `risk_of_ruin` (broken). **Copy only `default_rng(seed)`.**
- **Iteration count — a concrete answer, since the brief asked**: **use 10,000 for reported figures; 1,000 is the ecosystem's default for interactive work.** `arch`'s SPA/StepM/MCS all default to **`reps=1000`** (verified in the v8.0.0 constructor signature), which is the closest thing to an authoritative default in a maintained library here. The reasoning to apply, not just the number: bootstrap **Monte Carlo error on a quantile estimate falls as `1/sqrt(B)`**, so 1,000 reps gives ~3% relative error on a central estimate but is **visibly unstable in the tails** — and the tail *is* the entire point of a risk-of-ruin figure. 10,000 reps costs milliseconds on our data sizes (a few hundred trades × 10k paths is trivial numpy) and cuts that error ~3×. **There is no reason to be cheap here.** The check that settles it empirically: run the bootstrap twice with different seeds and compare the 5th-percentile max-DD; if they disagree materially, raise B until they don't. **Pin B and the seed as constants so the output is reproducible.**
- **Honesty requirement**: `replace=True` is what generates tails a permutation cannot. And report bands, not a point estimate — the whole reason to do this is to show uncertainty, not to manufacture a new confident number. State iteration count and seed in the output.
- **Caveat to decide**: an iid bootstrap **destroys serial correlation**. If EA trades are autocorrelated (streaks), iid understates clustered-loss risk. `arch`'s `StationaryBootstrap` exists precisely for this — but that is 71 MB for one class. **Start iid, document the assumption, revisit if streak data shows autocorrelation.**

### 5.2 Hand-roll PSR from Bailey & López de Prado ⭐ high value

- **What**: `PSR = Φ((SR - SR_benchmark) * sqrt(n-1) / sqrt(1 - skew*SR + ((kurt-1)/4)*SR²))` — the probability our observed Sharpe exceeds a benchmark given skew and kurtosis. Optionally MinTRL.
- **Why**: it is the direct, published answer to *"is this Sharpe real or is it noise at this sample size?"* — a question our validator currently answers with a hand-set tolerance band that merely **widens at low N** (`validator.py:261-279`). PSR makes the sample-size adjustment **principled rather than hand-tuned**, and it composes with our existing "Significancia" label (`validator.py:134-143`), which today is just `>=100 → "Alta"`.
- **Cost**: ~10 lines. **`scipy.stats.norm.cdf` — and `scipy` is already in `requirements.txt`.** (Note: §7 — scipy is currently a *phantom* production dependency. This would make it real, which is arguably a cleanup.)
- **License / dep risk**: **none.** Formulas are not copyrightable; reimplementing from a published paper carries no exposure. **This is what makes hand-rolling strictly better than depending on any of the encumbered options.**
- **Critical**: **do not port QuantStats' version.** Its kurtosis term double-subtracts 3, its `rf` handling mixes annualized and per-period units, and its `annualize=True` returns 15.78 for a probability. Take the formula from the paper; use QuantStats only as a worked example of what to avoid.
- **Applied to our data**: our Sharpe is per-trade and non-annualized, so PSR's `n` is the trade count and everything stays in per-trade units. **That is consistent and fine** — but it must be documented, because PSR's literature assumes returns.

### 5.3 Adopt `nautilus_trader`'s *architecture*, not the dependency ⭐ conceptual

Two ideas, both directly relevant, neither requiring the 110-183 MB install:

**(a) The data-shape contract.** `nautilus_trader`'s `PortfolioStatistic` trait declares **separate entry points per data shape** — `calculate_from_returns`, `calculate_from_realized_pnls`, `calculate_from_positions` — and **each statistic implements only the ones it is valid on, returning `None` for the rest**. `Expectancy` and `WinRate` implement `from_realized_pnls` and return `None` from `from_returns`; `SharpeRatio` and `ProfitFactor` do the inverse. The dispatcher treats `None` as *"this statistic does not apply to this data shape"*.

**This is the type-level expression of the exact defect that makes QuantStats wrong for us.** It also rhymes closely with our own SIN DATOS contract — `_nd_result`, `_completeness_missing`, the `"N/D"` discipline — which exists to stop confident numbers appearing next to missing inputs. The two are not the same mechanism (theirs is a type-level dispatch contract; ours is a runtime completeness gate) but they encode the same principle: **a statistic that does not apply to the data must say so, not return a number.** Worth citing when someone next asks why the SIN DATOS plumbing is so pedantic.

**(b) The correct way to annualize irregular data.** Nautilus's Sharpe **does not** annualize by elapsed wall-clock time, and does not blindly `sqrt(252)` raw trades. It **downsamples irregular returns into daily UTC bins by geometric compounding** (`(1+r1)(1+r2)-1`), *then* annualizes the daily mean/std ratio by a declared constant defaulting to 252 (`calculate_std` uses ddof=1, like us).

**That is the principled answer to §2.3's open question** — restore the assumption before relying on it, rather than pretending it holds. If we ever want an annualized Sharpe, **this is the recipe**: bin to daily equity, then annualize. The trade-off is that it discards trade identity, which is why it should be an *additional* metric, never a replacement for our per-trade Sharpe.

- **Cost**: (a) is free — it is a design principle we already follow; the value is in *naming* it. (b) is ~15 lines if we ever want annualized Sharpe, and **zero if we don't** — it is a recipe held in reserve, not a task.
- **License / dependency risk**: **none — nothing is imported.** This is the whole point of the recommendation. Taking the *package* would be LGPL-3.0, 110-183 MB per wheel, Python >=3.12,<3.15, plus `pyarrow` and an exact `fsspec==2026.2.0` pin (§5.8). **Taking the idea costs nothing and carries no license exposure**, since architecture and formulas are not copyrightable.

### 5.4 Consider vectorbt's drawdown episode model — concept only 🟡 medium

- **What**: model drawdowns as **records** (`peak_idx, valley_idx, end_idx, peak_val, valley_val, status ∈ {Active, Recovered}`) with a per-episode peak reset, rather than only a running-peak scalar.
- **Benefit**: a running-peak max-DD **structurally cannot express** "how many distinct drawdowns, how deep each, how long to recover, and is the current one still open." Our `stagnation_days` gestures at this but can't answer it. It would also let us honor vectorbt's own warning — **Active drawdowns may skew averages** — which we currently have no way to even represent.
- **Cost**: **~50 lines hand-rolled.** Do **not** take the dependency: Numba is a heavy transitive cost for a report analyzer, and vectorbt is **Commons Clause** (not OSI, forbids selling).
- **Honest caveat**: this is a **feature**, not a correctness fix. Our max-DD is already right (§2.1). **Only do this if drawdown episodes are a product requirement** — otherwise it is scope creep dressed up as rigor.

### 5.5 Amend the SQN entry in `known-issues.md`, and add the R-multiple finding ⭐ zero cost

**Status: applied on branch `pb-followup`** — all three parts below are done (see the status note at the top of §2.2).

Covered in §2.2. **What**: (a) soften the Tharp-cap entry from "divergencia contra el estándar" to an explicit unverified-attribution note; (b) add the **R-multiple vs raw P&L** divergence, which is better evidenced and currently unrecorded; (c) fix the xfail's `reason` string (`test_diff_metrics.py:664-675`) rather than renaming the test.
**Benefit**: the ledger's contract is *"todo lo de acá está probado"* (`known-issues.md:5`). **An unverified attribution violates that contract.** An entry that overstates its evidence is worse than no entry, because the ledger's whole value is that you can trust it without re-deriving it.
**Cost**: a doc edit and a string edit. **License / dep risk**: none.

### 5.6 Rename `wfe` — it is not Walk-Forward Efficiency ⭐ zero cost, removes an overclaim

**Status: applied on branch `pb-followup`** — see `known-issues.md` §7 (RESUELTO entry). All three actions below were done: `wfe` is now `live_vs_bt_profit_ratio` throughout, the `templates/validator.html` legend (now at lines 474-483) lists the four real bands, and the vestigial 50% row was dropped from `metrics-formulas.md` §16.

Covered in §4.5. **What**: rename `wfe` → `live_vs_bt_profit_ratio` (display *"Realización BT %"*); fix the `templates/validator.html:477-479` legend, which invents a 50% band and **inverts the signal** by calling 50-70% *"Aceptable"* where the code raises ALERTA; drop the vestigial 50% row from `metrics-formulas.md:341-343` that likely seeded it.
**Benefit**: `WFE` imports a specific promise from Pardo's literature — *your optimization generalizes out-of-sample* — that this number cannot make and, given our aggregate-only inputs, **structurally never could**. A reader seeing `WFE: 85%` may believe walk-forward validation happened. It did not.
**Cost**: a rename plus two doc/template edits. **Zero behavior change** — the metric is informational-only and never scored. **License / dep risk**: none.

---

### 5.7 What these projects deliberately DO NOT do — negative results

The brief asked for this explicitly and an earlier draft only scattered it. Consolidated, these are the **upstream non-goals**, distinct from §5.8's "what *we* shouldn't adopt":

- **QuantStats deliberately does not do trade-level analysis, and says so.** Its README carries a whole section conceding the limit rather than papering over it (§0). `stats.py` contains **no entry/exit analysis functions at all** — the absence is a scope decision, not an oversight. **The lesson: it told the truth about its boundary, and that honesty is what makes the boundary discoverable. Most libraries don't.**
- **QuantStats deliberately has no SQN.** Architecturally consistent: SQN needs a trade list; QuantStats consumes return series. **A library declining to compute a metric it cannot compute correctly is the right call** — and is exactly what nautilus formalizes in types (§5.3a).
- **vectorbt deliberately warns against its own default.** *"`Drawdowns` return both recovered AND active drawdowns, which may skew your performance results."* **This is anti-marketing** — a library flagging a footgun in its own defaults, with an `incl_active=False` affordance beside it. It raises credibility rather than lowering it.
- **`arch` deliberately does not implement White's Reality Check separately** — `class RealityCheck(SPA): pass`, `# Shallow clone of SPA`. It declines to duplicate a procedure that SPA generalizes, and the reference docs say so plainly rather than advertising two features.
- **No surveyed library caps SQN at N=100**, and none applies a data-spacing check to Sharpe. **These are non-goals by omission, and they are not all virtuous** — §2.3's silent `sqrt(252)` is a place where declining to validate is a *defect*, not a principled boundary. **Negative results cut both ways: the ecosystem's silence is sometimes wisdom and sometimes just absence.**
- **`skfolio` and `vectorbt` ship walk-forward *splitters* but deliberately no WFE metric** (§4.5). They provide the mechanism and leave the ratio and its interpretation to the user — declining to bless a threshold nobody can attribute to a primary source. **Given that we could not verify Pardo's own "50%" either, that reticence looks like good judgment.**

---

### 5.8 What NOT to do

| Don't | Why |
|---|---|
| Adopt `empyrical`/`quantstats`/`pyfolio` for trade metrics | **Semantically wrong**, not merely overkill. §0 |
| Promote `empyrical-reloaded` to production | "Usable, not maintained" (refuted 0-3). Fine as a test-only oracle. §1 |
| Depend on `backtrader` | 3.2y dormant; **GPL-3.0**; `requires_python` empty so pip installs it on unsupported interpreters. **Reference the formula, don't import it.** |
| Depend on `nautilus_trader` | **110-183 MB per wheel**; Python >=3.12,<3.15 (drops 3.11); pulls `pyarrow` (+19-39MB) and pins `fsspec==2026.2.0` exactly; no wheels for macOS x86_64/musl → **falls back to sdist requiring a full Rust toolchain.** Steal the architecture. |
| Depend on `vectorbt` | **Commons Clause — not OSI open source; forbids selling** software whose value derives substantially from it. For an analyzer whose value *is* trade statistics, that plausibly bites. |
| Depend on `backtesting.py` | **AGPL-3.0** — the network clause reaches SaaS, not just distribution. |
| Take `arch` for SPA today | 71 MB (pulls `statsmodels`), bus factor 1 — **and SPA answers a different question than `validator.py` asks.** §4.3 |
| Port QuantStats' `risk_of_ruin` or PSR | Both defective. §3.2, §4.2 |
| Use López de Prado's `.py.txt` scripts | **Licensing hazard** (non-standard GPL with GPL§7-disallowed restrictions); not APIs. §4.1 |

**Licensing summary — this is a first-order constraint.** **Every trade-level library we examined is encumbered**: `backtesting.py` **AGPL-3.0**, `backtrader` **GPL-3.0**, `nautilus_trader` **LGPL-3.0** (and LGPL "linking" semantics for pure-Python imports are legally unsettled), `vectorbt` **Commons Clause**. Among the packages whose licenses we actually verified, `arch` (**NCSA**) and `arbitragelab` (**BSD-3**) are the only clean permissive ones — and neither has what we'd want.

⚠️ **Scope correction, raised by a judge**: that is **not** a claim that permissive options are scarce in this space generally. **We did not verify licenses for `skfolio`, `riskfolio-lib`, `ffn` or `bt`**, all of which are in the survey and are publicly understood to be permissive (skfolio/riskfolio-lib BSD-3, ffn/bt MIT). They are irrelevant here because **none implements what we need** (§4.1), not because of licensing. **The encumbrance finding applies to the trade-level libraries specifically.**

**The formulas themselves are not copyrightable: reimplementing SQN or PSR from a published definition carries no exposure; importing these packages does.** If RTB-Flow is ever commercial or closed-source, **hand-rolling isn't just cheaper — it's the only unencumbered path.**

---

## 6. How they test their math — and why our harness beats every finance library surveyed

This was the brief's part (4). The finding is genuinely surprising, and it is a strong retrospective validation of P-A.

**Of nine projects examined, only `scipy` and `pandas` use property-based testing.** `arch`, `statsmodels`, `quantstats`, `backtesting.py`, `skfolio`, `vectorbt`, and **both** `empyrical` forks have **zero** hypothesis usage — verified by grepping complete repo tarballs at HEAD, with scipy/pandas as a positive control. **Not one finance library in the survey uses property-based testing.**

**We do.** `tests/oracle/test_prop_*.py`, `@settings(max_examples=200, deadline=None)`, with the convention stated at `test_prop_metrics.py:5-6`: *"un contraejemplo real de Hypothesis se documenta con `@pytest.mark.xfail(strict=True)` y el repro mínimo, **nunca se debilita la propiedad para forzarla a pasar**."*

**What the survey found, project by project:**

| Project | Discipline |
|---|---|
| **scipy** 🥇 | The gold standard, and it is *far* ahead. Differential testing **inside one implementation**: every distribution function computed by multiple independent methods (`formula`, `log/exp`, `complement`, `quadrature`, `inversion`, `cache`) and all asserted to agree, driven by hypothesis. **Old-vs-new** differential testing at rtol=1e-7. Reference fixtures from **mpmath at 40-1000 decimal digits** and Wolfram/Mathematica, with the derivation **checked in as runnable code**, tolerances to rtol=1e-15. In the strongest cases it computes the reference at **two precisions (dps=250, then dps=400)** to *prove* the value is exact to 64-bit. Its conftest defaults hypothesis to a **derandomized** profile. And its exception list candidly records `3: {'pareto'},  # stats.pareto is just wrong` — **naming its own oracle as the defective side.** |
| **statsmodels** | Checks the **actual R and Stata generator scripts** into the test tree (42 `.R`, 14 `.do`) beside the hardcoded numbers. **But**: nothing invokes them — no CI runs R — and some aren't even runnable (C-style `/* */` comments, absolute paths into the author's home dir). They **document provenance, they don't reproduce it.** 1,290 `assert_allclose` in three subpackages alone. |
| **arch** | Differential testing against **naive reimplementations hand-written in the test body**, at numpy's *default* tolerance — `test_multiple_comparison.py` has **zero explicit rtol/atol overrides**. EViews cross-checks are hardcoded constants; **the only R cross-check in the bootstrap module is commented out**, and its live assertions pin arch's own outputs as regression baselines. |
| **empyrical** (both forks) | One genuine bright spot: **differential-tests its own alpha/beta against `scipy.stats.linregress`** at 8 decimals, and the check is **non-circular** (its own covariance-based path vs scipy's). Also a **proxy-class architecture** re-running the same suite against pd.Series / np.ndarray / int-indexed Series, asserting return types and that **inputs are never mutated**. |
| **skfolio** | Differential-tests against **sklearn** (hand-written naive per-observation reference vs its vectorized version) at atol=1e-8/rtol=1e-6, in CI on 3 OSes. Tolerances span 11 orders of magnitude **chosen by test kind**: rtol=1e-12 for deterministic linear algebra, **atol=0.2 for statistical parameter recovery** — justified in-source ("parameter estimation for Johnson SU can be challenging"). **The tolerance encodes estimator sampling error, not float error.** That principle is worth stealing. |
| **backtesting.py** | `unittest`, not pytest. **One test file.** Its central math test pins **31 self-generated golden values** at rtol=1e-8 — expected values are **prior outputs of backtesting.py itself** (betrayed by the comment *"These values are also used on the website!"* and float noise like `51422.98999999996`). **No independent authority anywhere.** rtol=1e-8 is a **regression threshold, not an accuracy claim.** |
| **vectorbt** | **No automated cross-validation against empyrical.** The comparison lives in a **Jupyter notebook** using `print()` and `%timeit`, with **zero assertions**, **not collected by pytest** — it cannot fail a build. The real suite pins self-generated golden floats from a global `seed = 42` with **no explicit tolerance**, relying on `math.isclose` defaults. |
| **quantstats** | Ground truth, re-established (round 1's claim here was refuted, so this was checked from source): **0 hypothesis, 0 `assert_allclose`, 0 `parametrize`, 0 `pytest.approx`** — those absences are literally true. **But** the "only smoke checks" framing is **false**: across 125 test functions there are 3 tolerance assertions, 3 pandas comparisons, 3 `pytest.raises`, and **genuine seed-reproducibility tests**. **Zero reference fixtures from any independent authority** — all 6 numeric assertions compare quantstats **against itself**. No data fixture files at all. Ironically, **it is the one project that tests the seed contract itself as behavior.** |

**Where this leaves us.** Our `tests/oracle/` harness — characterization / property / differential, with **named tolerance constants each justified by production's rounding** (`TOL_SHARPE = 0.006  # metrics.py:391 rounds 2dp; numpy vs empyrical ULP`) — is **more disciplined than every finance library surveyed** and is structurally the same shape as scipy's approach, which is the best in the industry.

Three concrete refinements the survey does justify:

1. **`skfolio`'s principle: let the tolerance encode the kind of error.** Our constants encode *rounding* error. A bootstrap (§5.1) introduces **sampling** error, which is orders of magnitude larger. When we test it, the tolerance must say so — and say why, in the constant's comment, the way skfolio does.
2. **`scipy`'s two-precision trick, if we ever need a high-assurance reference.** Computing a reference at dps=250 and dps=400 and showing they agree *proves* the fixture rather than asserting it. Our binomial oracle already gets this right in spirit (exact `math.comb` vs scipy's log-gamma at `TOL_BINOMIAL_P = 1e-9`).
3. **`scipy`'s candour is the cultural lesson.** `# stats.pareto is just wrong` — naming its own oracle as defective — is exactly the register of our `known-issues.md`. **That register is an asset. Keep it.** (And §2.2 is the reminder that the ledger only keeps its value if wrong entries get fixed.)

**The meta-finding**: the defects this report catalogs — degenerate `goal_probability`, underflowing `risk_of_ruin`, PSR returning 15.78, docs not reproducible from the implementation — are **precisely what a reference-fixture test would have caught.** They survive *because* those libraries don't have one. **We should assume we must build this discipline ourselves, because the ecosystem demonstrably has not.** P-A was the right call, and this report is the receipt.

---

## 7. Incidental finding — `requirements.txt` carries three test-only packages

**`scipy>=1.11.0` sits in `requirements.txt` (production) but production code imports it nowhere.** `_binomial_p_value` is pure `math.comb` (`incubation_validator.py:252-268`). The only importer is `tests/oracle/test_diff_metrics.py:21`. It is a **phantom production dependency** — *unless* we adopt §5.2 (PSR), which needs `scipy.stats.norm.cdf` and would make it real. **Decide those two together.**

**And a judge caught that scipy is not alone.** All six lines of `requirements.txt`:

```
Flask>=3.0.0        # production
openpyxl>=3.1.0     # production
numpy>=1.26.0       # production (metrics.py:9)
scipy>=1.11.0       # TEST-ONLY — tests/oracle/test_diff_metrics.py:21
pytest>=8.0.0       # TEST-ONLY — self-evidently
html5lib>=1.1       # TEST-ONLY — only tests/test_frontend_contracts.py
```

**Three of six production requirements are test-only.** `pytest` in a production requirements file is the clearest tell. Verified: `html5lib`'s sole importer repo-wide is `tests/test_frontend_contracts.py`. **If we do this cleanup, do all three, not just scipy** — otherwise we fix the subtle instance and leave the obvious ones.

**This is a fossil, not sloppiness — and the fossil explains itself.** `tests/test_frontend_contracts.py:12-16` records the original reasoning verbatim:

> "html5lib is not currently pinned in requirements.txt; it is already installed in this environment. Per task instructions, since requirements.txt already pins other test-only deps (pytest) directly (there is no separate [dev requirements file])…"

So the decision was **correct when it was made**: there was no `requirements-dev.txt`, `pytest` was already precedent, and following precedent was right. **`requirements-dev.txt` was created later** — for the P-A oracle harness (`hypothesis`, `empyrical-reloaded`, `pytz`) — and **the pre-existing test-only deps were never migrated**. The file split happened; the backfill didn't.

⚠️ **Two snags if you act on this.** (1) That docstring's first clause is **false today** (`requirements.txt:6` *does* pin html5lib) and would become **true again** the moment you move it — a stale comment that self-heals is still a trap. **Fix the docstring in the same change.** (2) Anything installing from `requirements.txt` alone and then running tests would break. **Check CI before moving `pytest`** — that one is load-bearing in a way `scipy` and `html5lib` are not.

Related: `docs/metrics-formulas.md:386-391` **falsely claims** the binomial gate uses scipy with a normal-approximation fallback. It does not — pure `math.comb`, no fallback. Already logged as **D1** (`known-issues.md:691-694`) and pinned by `test_diff_metrics.py:721-739`. **Mentioned here because §7 and D1 are the same underlying confusion about what scipy does for us.**

---

## 8. Open questions this report does NOT close

Stated so they are not over-read as answered:

1. **Should SQN be capped at large N?** The survey **weakens the premise** (all three implementations found are uncapped; the cap's attribution to Tharp is unverified and his book is paywalled) but **does not answer the question, and could not settle the attribution in either direction.** Uncapped SQN provably grows as `sqrt(N)` without bound. That is a **display-stability policy decision that is ours alone.** §2.2
2. **Should SQN use R-multiples instead of raw P&L?** Tharp's definition is over the **R-multiple distribution** (high confidence). We — and backtrader, backtesting.py, vectorbt — feed it raw currency P&L. **This is the better-evidenced divergence and it is currently in neither our docs nor our tests.** Adopting it needs per-trade initial risk (SL distance × size), which the MT5 export may or may not carry reliably — **check the data before deciding.** §2.2
3. **What is the right annualization basis for our Sharpe?** Nautilus shows a principled recipe (§5.3) but it discards trade identity. Un-annualized per-trade Sharpe is honest but not comparable to industry figures. **Deserves its own decision record.** No library resolves this.
4. **Are EA trade sequences autocorrelated?** Decides iid vs stationary bootstrap in §5.1 — and therefore whether `arch`'s 71 MB is ever justified. **Answerable from our own data**, and should be, before choosing.
5. **Do the operator-typed SQX Monte Carlo numbers mean what we assume?** `known-issues.md` §3 says unverifiable. **§5.1 sidesteps it rather than resolving it** — a bootstrap over our own trades needs no cross-vendor semantic agreement. Whether to *keep* the SQX inputs alongside is a separate call.
6. **Is RTB-Flow commercial/closed-source?** Determines whether the licensing constraint in §5.8 is **decisive** or merely **informational**. It changes nothing about the recommendations — all of them are hand-roll — but it changes the strength of the argument against every alternative.

---

## 9. Coverage limits and honesty notes

- ⚠️ **The two weakest claims here are about published DEFINITIONS, not code.** Van Tharp's SQN and Pardo's WFE both live in **paywalled books**, and `vantharpinstitute.com` **hard-403s every URL**. So the SQN cap's attribution to Tharp is **unverified in both directions** (§2.2), and Pardo's "WFE ≥ 50%" is **received wisdom repeated everywhere and confirmed nowhere in his own words** (§4.5). **Everything here about *source code* was fetched and adversarially verified; these two were not, and could not be.** Distrust these first.
- **An earlier draft of this report was wrong, and blind judges caught it.** It asserted `known-issues.md` was "factually wrong" about the Tharp cap on the strength of *"was not established"* — absence of evidence presented as evidence of absence, in a report whose own stated method is "every load-bearing claim was fetched from a primary source," **with no Tharp source in its bibliography at all**. It also applied absence-of-evidence caveats to *weaker* claims while exempting its most consequential one. **§2.2 is the corrected version and is deliberately weaker than what it replaced.** The judges further caught: an overstated drawdown equivalence our own ledger already contradicts (§2.1); "the entire ecosystem" generalized from N=3; a license-scarcity claim broader than what was verified; a proposed test rename that would have been *less* accurate than the name it replaced; and three test-only packages in `requirements.txt` where the draft named one.
- **§4.5 exists because a judge caught that the word "walk-forward" appeared nowhere** in a report answering a brief that asked for it by name — while `validator.py` ships a feature called Walk-Forward Efficiency. **That was the largest miss, and review found it, not the research.**
- **Round 1 self-reported two dead gaps** (validation statistics; self-testing). **Round 2 was built specifically to close them**, and did. This report reflects both rounds.
- **Refuted claims were dropped, not softened.** 11 died across both rounds, including several that would have made this report punchier — e.g. "`pypbo` is the only maintained full-family implementation" (1-2), and "vectorbt is the only library that refuses to compute annualized metrics and warns instead" (1-2). **They are absent because they did not survive, not because they were inconvenient.**
- **Several surviving claims were sustained on evidence different from what was first cited.** QuantStats' Monte Carlo is in `_montecarlo.py`, **not** `stats.py`; vectorbt's DD denominator had to be cited to `generic/nb.py`/`returns/nb.py` because the doc page **genuinely does not state the formula** and the original quote argued from silence. Those are corrected above.
- **Absence-of-evidence limits**: "no SQN in nautilus" rests on a filename-level tree query over the v1.230.0 tag plus code search — **not** on reading every analysis file. "No maintained SPA outside `arch`" is **weaker-grounded** than the PSR/DSR/PBO findings (which rest on direct source inspection): no PyPI-wide index scan was run, so a package under an unguessed name could exist.
- **Branch-vs-release**: backtesting.py, vectorbt and nautilus quotes are from default branches, which run ahead of released wheels (vectorbt's master is ~9 days and 3 commits past the 1.1.0 wheel, **touching the exact rolling-std/ddof surface this report reasons about**). Pin a SHA if an exact line number ever becomes load-bearing.
- **Editorial attributions**: "the ecosystem is **wrong** for us, not overkill" is **this report's framing**, not upstream's. QuantStats' own register is softer ("may differ from trade-level statistics") — but **the documented behavior entails the conclusion regardless of phrasing.** "Catastrophically wrong" (`risk_of_ruin`) is evaluative, and derived directly from arithmetic reproduced independently.

---

## 10. Sources

Primary (fetched 2026-07-17):
- `quantopian/empyrical` — `empyrical/stats.py`, `empyrical/periods.py`
- `stefan-jansen/empyrical-reloaded` — `src/empyrical/stats.py`, `pyproject.toml`, releases
- `stefan-jansen/pyfolio-reloaded` — releases
- `ranaroussi/quantstats` — `quantstats/stats.py`, `quantstats/_montecarlo.py`, `quantstats/utils.py`, `docs/montecarlo.md`, `tests/`, issues #298 / #259 / #71
- `mementum/backtrader` — `backtrader/analyzers/sqn.py`, `backtrader/mathsupport.py`
- `kernc/backtesting.py` — `backtesting/_stats.py`, `backtesting/test/_test.py`, README
- `polakowo/vectorbt` — `generic/nb.py`, `generic/drawdowns.py`, `generic/enums.py`, `returns/nb.py`, `returns/metrics.py`, `returns/accessors.py`, `portfolio/trades.py`, `tests/`
- `nautechsystems/nautilus_trader` — `crates/analysis/src/statistic.rs`, `statistics/sharpe_ratio.rs`, `statistics/expectancy.rs`, `nautilus_trader/analysis/analyzer.py`
- `bashtage/arch` v8.0.0 (`038d78b`) — `arch/bootstrap/multiple_comparison.py`, `arch/bootstrap/base.py`, `arch/bootstrap/__init__.py`, `pyproject.toml`, `arch/tests/`
- `hudson-and-thames/mlfinlab` — `backtest_statistics/statistics.py`, `LICENSE.txt`; `hudson-and-thames/arbitragelab` — `LICENSE.txt`
- `skfolio/skfolio`, `dcajasn/Riskfolio-Lib`, `pmorissette/ffn`, `pmorissette/bt`
- `scipy` — `scipy/stats/tests/test_continuous.py`, `conftest.py`; `pandas-dev/pandas` — `pandas/conftest.py`, `pandas/_testing/asserters.py`, `pandas/_testing/_hypothesis.py`, `pandas/tests/tseries/offsets/`; `statsmodels`
- PyPI JSON API and GitHub REST API for all liveness data
- `quantresearch.org/Software.htm` — López de Prado's reference scripts
- Bailey & López de Prado, *The Deflated Sharpe Ratio* (davidhbailey.com/dhbpapers/deflated-sharpe.pdf)
- arXiv 1905.08042 — serial correlation and annualized Sharpe overstatement
- NumPy NEP-19 — RNG policy (`default_rng` vs legacy `RandomState`)

Secondary / could not reach primary (⚠️ labeled as such throughout):
- **Van Tharp, SQN**: `vantharpinstitute.com` and `vantharp.com` — **HTTP 403 on every URL**; *Definitive Guide to Position Sizing* (2008) paywalled, no quotable excerpt. Definitional wording ("the R-multiple distribution") recovered only via TradingView's attributed reproduction of Tharp Institute glossary text. Cap claim traces to IndexTrader (blog) and a nexusfi forum thread, both hedged paraphrase. Counter-evidence: Jonathan Kinlay's algebraic derivation (uncapped); a Wealth-Lab user's self-attributed `Math.Min(10, ...)` modification.
- **Robert Pardo, WFE**: *The Evaluation and Optimization of Trading Strategies* (2008) and *Design, Testing and Optimization of Trading Systems* (1992) — paywalled/print-only. Best attributable secondary: TradeStation Walk-Forward Optimizer help (the TS WFO was built with Pardo) for both the annualized-IS/OOS-ratio definition and the "50% or more" threshold. Note Wikipedia's *Walk forward optimization* entry cites Pardo and carries **no threshold at all**.
