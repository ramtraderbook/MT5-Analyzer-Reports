"""
metrics.py - Performance metrics calculation for EA Analyzer
All metrics computed from net P&L (Profit + Commission + Swap).
"""

import math
from datetime import date, datetime, timedelta

import numpy as np

# A per-trade P&L series whose standard deviation is below this fraction of its
# mean is a fixed payout (e.g. a scalper with a constant take-profit and no
# losses yet), not a real distribution — SQN/Sharpe are not estimable on it.
MIN_COEFFICIENT_OF_VARIATION = 0.01

# Minimum trades before SQN earns a quality label. Below this the sample cannot
# support a grade at any variance, so the number is reported with the
# "(orientativo)" note but the label is withheld. Same threshold both guards.
MIN_TRADES_FOR_SQN_LABEL = 20

# Bootstrap Monte Carlo error on a quantile falls as 1/sqrt(B), so 1000 paths
# (arch's default for SPA/StepM/MCS -- the closest thing to an authoritative
# default found in a maintained library, docs/research/prior-art.md §5.1) is
# visibly unstable in the tail -- and the tail is the entire point of a ruin
# figure. 10k costs milliseconds at this repo's data sizes and cuts that error
# roughly 3x. There is no reason to be cheap here.
BOOTSTRAP_ITERATIONS = 10000

# Pinned so the bootstrap is reproducible; echoed in the result so the seed is
# legible from the output, not merely assumed by whoever reads it later.
BOOTSTRAP_SEED = 20260717

# Resampling a handful of trades manufactures false confidence -- the
# resampled multiset barely differs from the original sample at low N, so the
# "band" would just restate the point estimate with extra ceremony. Mirrors
# the MIN_TRADES_FOR_SQN_LABEL=20 rationale: same floor, same reasoning.
MIN_TRADES_FOR_BOOTSTRAP = 20

# A set of thresholds, not one magic "ruin" number: where the actual ruin
# point sits is a policy decision this repo has not made (see
# docs/known-issues.md §1, blocked on real data for a related calibration
# question) and must not be smuggled in here.
RUIN_THRESHOLDS_PCT = (10.0, 20.0, 30.0, 50.0)

# Bounds calculate_bootstrap_risk's peak allocation regardless of n or
# iterations. The previous all-at-once formulation peaked at 1121 MB at
# n=2000, iterations=10000 (measured with tracemalloc) -- ~7x the size of a
# single (iterations, n+1) array, because ~7 full-size arrays were
# co-resident (see calculate_bootstrap_risk's body for the accounting). 64 MB
# keeps a chunk comfortably small without making the loop overhead dominate
# at realistic n.
#
# Re-measured with tracemalloc after chunking, same iterations=10000, seed=42,
# three runs each with zero variance between runs (docs/known-issues.md §7):
# n=50 -> 29 MB peak (fits in one chunk, same as before -- the budget never
# forces chunking for small n), n=200 -> 71 MB, n=500 -> 77 MB, n=2000 -> 77
# MB. Peak plateaus near the budget instead of growing with n; it never
# approaches the previous 1121 MB (~14x lower at n=2000).
BOOTSTRAP_MEMORY_BUDGET_MB = 64


EA_COLORS = [
    "#4FC3F7",
    "#FF7043",
    "#66BB6A",
    "#AB47BC",
    "#FFA726",
    "#26C6DA",
    "#EC407A",
    "#8D6E63",
    "#78909C",
    "#D4E157",
    "#5C6BC0",
    "#FF8A65",
]

SQN_LABELS = [
    (7.0, "Santo Grial"),
    (5.0, "Sobresaliente"),
    (3.0, "Excelente"),
    (2.5, "Bueno"),
    (2.0, "Promedio"),
    (1.6, "Debajo promedio"),
    (0.0, "Pobre"),
]


def _sqn_label(sqn_val):
    for threshold, label in sorted(SQN_LABELS, key=lambda x: -x[0]):
        if sqn_val >= threshold:
            return label
    return "Pobre"


def _build_equity_curve(trades_sorted):
    """
    Build equity curve showing NET P&L from 0 (not absolute equity).
    The Y axis represents gains/losses relative to starting capital.
    Positive = profit, negative = loss.
    Returns list of {"date": iso_str, "equity": float}.
    """
    if not trades_sorted:
        return []

    # Find the first trade with a valid close_time for the initial anchor point
    # Trades with None close_time are sorted to the end — skip them for the anchor
    first_dt = None
    for t in trades_sorted:
        ct = t["close_time"]
        if ct is not None:
            first_dt = datetime.fromisoformat(ct) if isinstance(ct, str) else ct
            break

    if first_dt is None:
        # All trades have None close_time — cannot build a meaningful curve
        return []

    initial_date = (first_dt.date() - timedelta(days=1)).isoformat()

    pnl = 0.0
    curve = [{"date": initial_date, "equity": 0.0}]

    for trade in trades_sorted:
        close_dt = trade["close_time"]
        if close_dt is None:
            # Trades without close_time are excluded from the equity curve entirely.
            # Including their P&L silently would cause the curve to misrepresent
            # drawdown peaks — the curve would show a lower peak than what actually
            # occurred, making max DD appear artificially small.
            continue
        if isinstance(close_dt, str):
            close_dt = datetime.fromisoformat(close_dt)
        date_str = close_dt.date().isoformat()
        pnl += trade["net_pnl"]
        curve.append({"date": date_str, "equity": round(pnl, 2)})

    return curve


def _build_drawdown_curve(equity_curve, capital):
    """
    Build drawdown curve from P&L equity curve (values relative to 0).
    DD% uses (capital + peak_pnl) as denominator.
    Returns list of {"date": iso_str, "dd_pct": float (negative)}.
    """
    if not equity_curve:
        return []

    peak_pnl = 0.0  # P&L peak starts at 0
    dd_curve = []

    for point in equity_curve:
        pnl = point["equity"]  # net P&L value
        if pnl > peak_pnl:
            peak_pnl = pnl
        dd_dollar = peak_pnl - pnl
        peak_abs = capital + peak_pnl
        dd_pct = -(dd_dollar / peak_abs * 100) if peak_abs > 0 else 0.0
        dd_curve.append({"date": point["date"], "dd_pct": round(dd_pct, 4)})

    return dd_curve


def _calc_max_drawdown(equity_curve, capital):
    """
    Returns (max_dd_dollar, max_dd_pct, last_peak_date_str).
    equity_curve values are net P&L (starting from 0).
    DD% uses (capital + peak_pnl) as denominator.
    last_peak_date_str is the date of the most recent P&L peak.
    """
    if not equity_curve:
        return 0.0, 0.0, None

    peak_pnl = 0.0  # track P&L peak (starts at 0)
    max_dd_dollar = 0.0
    max_dd_pct = 0.0
    last_peak_date = equity_curve[0]["date"]

    for point in equity_curve:
        pnl = point["equity"]  # net P&L
        if pnl > peak_pnl:
            peak_pnl = pnl
            last_peak_date = point["date"]

        dd_dollar = peak_pnl - pnl
        if dd_dollar > max_dd_dollar:
            max_dd_dollar = dd_dollar

        # max_dd_pct is tracked independently as the maximum dd_pct over every
        # point — it must NOT be tied to the moment of maximum dollar drawdown,
        # since the peak_abs denominator changes between points and the worst
        # percentage drawdown can occur at a different point than the worst
        # dollar drawdown.
        peak_abs = capital + peak_pnl
        dd_pct = (dd_dollar / peak_abs * 100) if peak_abs > 0 else 0.0
        if dd_pct > max_dd_pct:
            max_dd_pct = dd_pct

    return round(max_dd_dollar, 2), round(max_dd_pct, 4), last_peak_date


def calculate_bootstrap_risk(
    net_pnl_list, capital, iterations=BOOTSTRAP_ITERATIONS, seed=BOOTSTRAP_SEED
):
    """
    iid bootstrap over per-trade net P&L: resample WITH REPLACEMENT to build a
    Monte Carlo distribution of max drawdown, and report percentile bands plus
    breach probabilities against a set of thresholds -- never a single point
    estimate, since showing uncertainty is the entire purpose.

    Why this exists: the DD gate elsewhere in this repo leans on operator-typed
    StrategyQuant numbers whose semantics docs/known-issues.md §3 admits are
    NO VERIFICABLE (we cannot confirm SQX defines "max DD %" the way this repo
    does). A bootstrap over OUR OWN trades removes that dependency by
    construction: the same definition applies on both sides.

    Method, each detail load-bearing (docs/research/prior-art.md §3.1, §3.2,
    §5.1):
    - `np.random.default_rng(seed)` is the modern Generator (PCG64). Never the
      legacy `np.random.RandomState`, and this never touches numpy's global
      random state via `np.random.seed`.
    - Resampling uses `replace=True`. That is what lets the worst loss be
      redrawn repeatedly and produce the fat tails a ruin figure needs. A
      PERMUTATION (quantstats' approach) is degenerate: it preserves
      sum(net_pnl), so every simulated path ends at the identical terminal
      value and the tail is structurally unreachable. Do not shuffle.
    - Each simulated path's running peak floors at 0 and its drawdown % uses
      (capital + peak_pnl) as the denominator -- the SAME convention as
      _calc_max_drawdown (metrics.py:194), by prepending a 0.0 anchor column
      before the cumulative sum, mirroring how _build_equity_curve/
      _calc_max_drawdown start from an implicit 0 P&L point. This consistency
      is the point: it lets a differential test compare the two directly.
    - Paths are drawn in CHUNKS from a single `rng` created once up front, so
      the draw stream -- and therefore every result -- is identical to drawing
      all `iterations` paths at once; only peak memory changes. See
      BOOTSTRAP_MEMORY_BUDGET_MB below for why.

    Returns a dict with `"available": False` and a `"reason"` string (the
    repo's SIN DATOS convention -- see _calc_sqn) when:
    - net_pnl_list is empty/None ("sin trades"),
    - fewer than MIN_TRADES_FOR_BOOTSTRAP trades ("insuficientes datos"),
    - capital <= 0 ("capital no positivo" -- deliberately NOT the silent 0.0
      that _calc_max_drawdown's `peak_abs > 0 else 0.0` guard produces;
      reproducing a known, uncorrected defect in new code would be
      inexcusable),
    - the input contains any NaN/inf ("valores no finitos" -- never silently
      propagated, see docs/known-issues.md §"A2" for what happens elsewhere in
      this repo when a NaN is allowed to evaporate silently instead),
    - iterations < 1 ("iterations invalido" -- np.percentile on an empty array
      would otherwise raise, breaking this function's own "not estimable ->
      structured absence" contract; iterations == 1 is degenerate but valid
      and DOES run).

    Otherwise returns a dict with `"available": True` plus:
        max_dd_pct_p50 / _p95 / _p99: percentile bands of simulated max DD%.
        ruin_probability: {threshold: fraction of paths whose max DD% breached
            it} for each of RUIN_THRESHOLDS_PCT -- a set of thresholds, not one
            magic "ruin" number; the ruin point itself is a policy decision
            this repo has not made. Breach is `>= threshold`: touching the
            level counts, which is the conservative reading for a risk figure.
        iterations / seed: echoed for reproducibility.
        trades: n used.

    DELIBERATELY NOT WIRED -- this is NOT dead code, and the distinction from
    _calc_risk_of_ruin (deleted for having zero call sites, pinned by
    tests/test_metrics.py:397-401) is a deliberate capability with zero
    consumers today. Full rationale (COST/CONTRACT/POLICY) lives in
    docs/known-issues.md §7 -- read it there, not here, so the two never
    drift out of sync.
    """
    if not net_pnl_list:
        return {"available": False, "reason": "sin trades"}

    n = len(net_pnl_list)
    if n < MIN_TRADES_FOR_BOOTSTRAP:
        return {
            "available": False,
            "reason": f"insuficientes datos (n={n} < {MIN_TRADES_FOR_BOOTSTRAP})",
        }

    if capital is None or capital <= 0:
        return {"available": False, "reason": "capital no positivo"}

    arr = np.array(net_pnl_list, dtype=float)
    if not np.all(np.isfinite(arr)):
        return {"available": False, "reason": "valores no finitos"}

    # iterations < 1 would otherwise reach np.percentile on an empty array
    # (iterations == 0) or rng.choice with a negative size (iterations < 0),
    # both raising instead of honoring this function's own "not estimable ->
    # structured absence" contract. iterations == 1 is a single, degenerate
    # path but a perfectly valid bootstrap of size 1 -- it must NOT be
    # rejected here.
    if iterations is None or iterations < 1:
        return {
            "available": False,
            "reason": f"iterations invalido ({iterations} < 1)",
        }

    rng = np.random.default_rng(seed)

    # Process paths in chunks so peak memory is bounded by
    # BOOTSTRAP_MEMORY_BUDGET_MB regardless of n or iterations. The previous
    # all-at-once formulation held six full-size (iterations, n+1) locals
    # live simultaneously (sims, cum, peak, dd_dollar, peak_abs, dd_pct) plus
    # the np.concatenate temporary -- measured with tracemalloc at 1121 MB
    # peak for n=2000, iterations=10000, roughly 7x the size of a single one
    # of those arrays. "7" below is that measured count of co-resident
    # full-size arrays, not an exact accounting -- it is used only to size a
    # chunk, and a wrong guess only wastes or under-uses the budget, it never
    # produces a wrong result.
    #
    # Only per-path maxima are accumulated across chunks (one float per path
    # -- 10k floats is ~80 KB, negligible), so peak memory no longer grows
    # with `iterations`. It still grows linearly with `n` (each chunk is
    # still (chunk_size, n+1)), and wall-clock time still grows with
    # iterations * n regardless of chunking -- chunking removes the memory
    # cliff, which was the actual hazard (unbounded RAM can OOM the process),
    # not the linear time cost (a slow call degrades gracefully; it does not
    # crash the process). A caller passing a huge n or iterations now pays in
    # wall-clock time, not in a memory cliff. No arbitrary cap is added on
    # either -- there is no equivalent hazard to bound.
    budget_bytes = BOOTSTRAP_MEMORY_BUDGET_MB * 1024 * 1024
    chunk_size = max(1, int(budget_bytes // (7 * (n + 1) * 8)))
    chunk_size = min(chunk_size, iterations)

    max_dd_pct_per_path = np.empty(iterations, dtype=float)
    start = 0
    while start < iterations:
        end = min(start + chunk_size, iterations)
        m = end - start

        sims = rng.choice(arr, size=(m, n), replace=True)

        # Anchor each path at 0.0 before accumulating, matching
        # _build_equity_curve/_calc_max_drawdown's implicit 0 P&L starting
        # point -- this is what makes the running peak floor at 0 rather
        # than at the first (possibly negative) resampled trade.
        anchor = np.zeros((m, 1))
        cum = np.cumsum(np.concatenate([anchor, sims], axis=1), axis=1)
        peak = np.maximum.accumulate(cum, axis=1)
        dd_dollar = peak - cum
        peak_abs = capital + peak  # always > 0: capital > 0, peak >= 0 by construction
        dd_pct = dd_dollar / peak_abs * 100.0

        max_dd_pct_per_path[start:end] = dd_pct.max(axis=1)
        start = end

    p50, p95, p99 = np.percentile(max_dd_pct_per_path, [50, 95, 99])

    ruin_probability = {
        threshold: float(np.mean(max_dd_pct_per_path >= threshold))
        for threshold in RUIN_THRESHOLDS_PCT
    }

    return {
        "available": True,
        "max_dd_pct_p50": round(float(p50), 4),
        "max_dd_pct_p95": round(float(p95), 4),
        "max_dd_pct_p99": round(float(p99), 4),
        "ruin_probability": ruin_probability,
        "iterations": iterations,
        "seed": seed,
        "trades": n,
    }


def _calc_stagnation(last_peak_date_str):
    """Days from last equity peak to today. Evaluated at call time, not module load."""
    if not last_peak_date_str:
        return 0
    try:
        last_peak = date.fromisoformat(last_peak_date_str)
        return max(0, (date.today() - last_peak).days)
    except (ValueError, TypeError):
        return 0


def _calc_sharpe(net_pnl_list):
    """Simplified per-trade Sharpe = mean(R) / std(R, ddof=1). No risk-free rate."""
    n = len(net_pnl_list)
    if n < 2:
        return None
    arr = np.array(net_pnl_list, dtype=float)
    mean_r = float(np.mean(arr))
    std_r = float(np.std(arr, ddof=1))
    # std_r <= 0 only when std_r == 0, so this also subsumes the old exact-zero
    # check and never divides when mean_r == 0.
    if std_r <= abs(mean_r) * MIN_COEFFICIENT_OF_VARIATION:
        return None
    return round(mean_r / std_r, 2)


def _calc_sqn(net_pnl_list):
    """
    SQN = sqrt(N) * mean(R) / std(R, ddof=1)
    Returns (sqn_val or None, note_str, label_str)
    """
    n = len(net_pnl_list)
    if n < 2:
        return None, "(insuficientes datos)", "N/A"

    arr = np.array(net_pnl_list, dtype=float)
    mean_r = float(np.mean(arr))
    std_r = float(np.std(arr, ddof=1))

    # Guard on the coefficient of variation, not on an exact-zero epsilon: a
    # per-trade P&L series varying by less than MIN_COEFFICIENT_OF_VARIATION
    # of its mean is a fixed payout (e.g. a fixed-TP scalper with no losses
    # yet), not a real distribution — SQN is not estimable on it. This also
    # subsumes the old std_r == 0 check and never divides when mean_r == 0
    # (std_r <= 0 is true only when std_r == 0).
    if std_r <= abs(mean_r) * MIN_COEFFICIENT_OF_VARIATION:
        note = "(desviación cero)" if std_r == 0 else "(varianza degenerada)"
        return None, note, "N/A"

    sqn = math.sqrt(n) * mean_r / std_r
    note = "(orientativo)" if n < MIN_TRADES_FOR_SQN_LABEL else ""
    # Below MIN_TRADES_FOR_SQN_LABEL the sample cannot support a quality grade:
    # sqrt(N)*mean/std is unbounded for a small N, so an EA that merely opens
    # with a few wins scores as elite ([5.0, 6.0] -> 11.0 -> "Santo Grial").
    # Report the number with its "(orientativo)" note, but withhold the grade.
    label = _sqn_label(sqn) if n >= MIN_TRADES_FOR_SQN_LABEL else "N/A"

    return round(sqn, 2), note, label


def _calc_streaks(net_pnl_list):
    """
    Returns (max_wins, max_losses, avg_wins, avg_losses).
    """
    max_wins = max_losses = 0
    curr_win = curr_loss = 0
    win_streaks = []
    loss_streaks = []

    for pnl in net_pnl_list:
        if pnl > 0:
            curr_win += 1
            if curr_loss > 0:
                loss_streaks.append(curr_loss)
                curr_loss = 0
            max_wins = max(max_wins, curr_win)
        else:
            curr_loss += 1
            if curr_win > 0:
                win_streaks.append(curr_win)
                curr_win = 0
            max_losses = max(max_losses, curr_loss)

    # Flush remaining
    if curr_win > 0:
        win_streaks.append(curr_win)
    if curr_loss > 0:
        loss_streaks.append(curr_loss)

    avg_wins = round(sum(win_streaks) / len(win_streaks), 1) if win_streaks else 0.0
    avg_losses = (
        round(sum(loss_streaks) / len(loss_streaks), 1) if loss_streaks else 0.0
    )

    return max_wins, max_losses, avg_wins, avg_losses


def _calc_rolling_metrics(trades_sorted: list, window: int) -> list:
    """
    Calcula métricas rodantes (Expectancy, Win Rate, PF) sobre ventana de N trades.
    Retorna lista de puntos para graficar.
    """
    if len(trades_sorted) < window:
        return []

    result = []
    for i in range(window - 1, len(trades_sorted)):
        chunk = trades_sorted[i - window + 1 : i + 1]
        pnl = [t["net_pnl"] for t in chunk]
        wins = [p for p in pnl if p > 0]
        losses = [p for p in pnl if p <= 0]

        expectancy = round(sum(pnl) / len(pnl), 2)
        win_rate = round(len(wins) / len(pnl) * 100, 1)

        gp = sum(wins)
        gl = abs(sum(losses))
        pf = round(gp / gl, 3) if gl > 0 else None

        ct = chunk[-1]["close_time"]
        if hasattr(ct, "isoformat"):
            ct = ct.isoformat()
        # already a string — use as-is
        result.append(
            {
                "index": i + 1,
                "date": ct,
                "expectancy": expectancy,
                "win_rate": win_rate,
                "pf": pf,
            }
        )

    return result


def _weeks_operating(trades_sorted):
    """Weeks from first trade close_time to today.

    Using today (not the last trade date) correctly captures how long the EA
    has been running — including any recent inactivity period. The validator
    guard logic relies on this to detect EAs that stopped trading.
    """
    if not trades_sorted:
        return 0.0

    def to_dt(t):
        ct = t["close_time"]
        if isinstance(ct, str):
            return datetime.fromisoformat(ct)
        return ct

    first = to_dt(trades_sorted[0])
    if first is None:
        return 0.0
    delta = datetime.combine(date.today(), datetime.min.time()) - first
    # Clamp to 0.0: a trade closing today at an intraday time (after midnight)
    # would otherwise yield a tiny negative value, since the minuend is
    # today at 00:00 while `first` carries an intraday close time. You cannot
    # scale a DD limit over negative weeks — 0.0 is the correct floor.
    return max(0.0, round(delta.total_seconds() / (7 * 86400), 1))


def calculate_ea_metrics(ea_name: str, trades: list, config: dict) -> dict:
    """
    Calculate all metrics for a single EA.
    trades: list of trade dicts for this EA only.
    config: full config dict.
    Returns MetricsResult dict.
    """
    if not trades:
        return _empty_metrics(ea_name, config)

    # Sort by close_time
    def sort_key(t):
        ct = t["close_time"]
        if isinstance(ct, str):
            return datetime.fromisoformat(ct)
        return ct if ct is not None else datetime.max

    trades_sorted = sorted(trades, key=sort_key)

    net_pnl_list = [t["net_pnl"] for t in trades_sorted]

    # Trades with an unparseable close_time (parser.py _parse_date returns None).
    # Their P&L is still counted in net_profit/SQN/Sharpe/streaks but they are
    # excluded from the equity curve and therefore from max_dd/ret_dd — see
    # docs/metrics-formulas.md section 5.
    untimed_trades = sum(1 for t in trades_sorted if t["close_time"] is None)

    # P&L breakdown
    wins = [p for p in net_pnl_list if p > 0]
    losses = [p for p in net_pnl_list if p <= 0]

    gross_profit = sum(wins)
    gross_loss = sum(losses)
    net_profit = gross_profit + gross_loss

    total_trades = len(net_pnl_list)
    winning_trades = len(wins)
    losing_trades = len(losses)

    win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0.0
    avg_win = (gross_profit / winning_trades) if winning_trades > 0 else 0.0
    avg_loss = (gross_loss / losing_trades) if losing_trades > 0 else 0.0

    profit_factor = (
        (gross_profit / abs(gross_loss))
        if gross_loss != 0
        else (float("inf") if gross_profit > 0 else 0.0)
    )
    payout_ratio = (
        (avg_win / abs(avg_loss))
        if avg_loss != 0
        else (float("inf") if avg_win > 0 else 0.0)
    )
    expectancy = net_profit / total_trades if total_trades > 0 else 0.0

    best_trade = max(net_pnl_list) if net_pnl_list else 0.0
    worst_trade = min(net_pnl_list) if net_pnl_list else 0.0

    # Long/short breakdown
    long_trades = [t for t in trades_sorted if t["direction"] == "buy"]
    short_trades = [t for t in trades_sorted if t["direction"] == "sell"]
    long_wins = sum(1 for t in long_trades if t["net_pnl"] > 0)
    short_wins = sum(1 for t in short_trades if t["net_pnl"] > 0)

    # Avg duration
    durations = [
        t["duration_hours"]
        for t in trades_sorted
        if t.get("duration_hours") is not None
    ]
    avg_duration = round(sum(durations) / len(durations), 2) if durations else 0.0

    # Capital from config (default $5,000)
    mapping = config.get("mappings", {}).get(ea_name, {})
    capital = float(mapping.get("capital", 5000.0))

    # Equity & drawdown curves
    equity_curve = _build_equity_curve(trades_sorted)
    dd_curve = _build_drawdown_curve(equity_curve, capital)

    max_dd_dollar, max_dd_pct, last_peak_date = _calc_max_drawdown(
        equity_curve, capital
    )
    stagnation_days = _calc_stagnation(last_peak_date)

    # ret_dd and recovery_factor are the same ratio — calculated once.
    # ret_dd is the canonical key used internally and by the validator.
    # recovery_factor is kept as an alias in the output dict for template compatibility.
    ret_dd = (net_profit / max_dd_dollar) if max_dd_dollar > 0 else None

    sqn_val, sqn_note, sqn_label = _calc_sqn(net_pnl_list)
    sharpe = _calc_sharpe(net_pnl_list)
    max_wins, max_losses, avg_wins_streak, avg_losses_streak = _calc_streaks(
        net_pnl_list
    )
    weeks = _weeks_operating(trades_sorted)

    # Instrument from trades
    symbols = list(set(t["symbol"] for t in trades_sorted))
    instrument = symbols[0] if len(symbols) == 1 else ", ".join(sorted(symbols))
    magic = mapping.get("magic")
    alias = mapping.get("alias", "") or ea_name  # alias if set, else original name
    mapped_instrument = mapping.get("instrument", instrument)
    label = f"{magic} - {alias}" if magic else alias

    # Format helpers
    def fmt_pf(val):
        if val == float("inf") or val != val:
            return "∞"
        return round(val, 2)

    return {
        "ea_name": ea_name,
        "magic": magic,
        "label": label,
        "instrument": mapped_instrument or instrument,
        "total_trades": total_trades,
        "winning_trades": winning_trades,
        "losing_trades": losing_trades,
        "long_trades": len(long_trades),
        "short_trades": len(short_trades),
        "long_wins": long_wins,
        "short_wins": short_wins,
        "net_profit": round(net_profit, 2),
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        "best_trade": round(best_trade, 2),
        "worst_trade": round(worst_trade, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "win_rate": round(win_rate, 2),
        "profit_factor": fmt_pf(profit_factor),
        "payout_ratio": fmt_pf(payout_ratio),
        "expectancy": round(expectancy, 2),
        "max_dd_dollar": max_dd_dollar,
        "max_dd_pct": round(max_dd_pct, 2),
        "ret_dd": round(ret_dd, 2) if ret_dd is not None else None,
        "recovery_factor": round(ret_dd, 2) if ret_dd is not None else None,
        "sqn": sqn_val,
        "sqn_note": sqn_note,
        "sqn_label": sqn_label,
        "sharpe_ratio": sharpe,
        "weeks_operating": weeks,
        "avg_duration_hours": avg_duration,
        "stagnation_days": stagnation_days,
        "max_consec_wins": max_wins,
        "max_consec_losses": max_losses,
        "avg_consec_wins": avg_wins_streak,
        "avg_consec_losses": avg_losses_streak,
        "untimed_trades": untimed_trades,
        "equity_curve": equity_curve,
        "drawdown_curve": dd_curve,
        "trades": trades_sorted,
    }


def calculate_portfolio_metrics(all_trades: list, config: dict = None) -> dict:
    """
    Calculate portfolio-wide metrics using all closed trades combined.
    Portfolio capital = sum of each EA's capital from config (default $5,000 each).
    """
    if config is None:
        config = {}
    if not all_trades:
        return _empty_metrics("PORTFOLIO", {})

    def sort_key(t):
        ct = t["close_time"]
        if isinstance(ct, str):
            return datetime.fromisoformat(ct)
        return ct if ct is not None else datetime.max

    trades_sorted = sorted(all_trades, key=sort_key)
    net_pnl_list = [t["net_pnl"] for t in trades_sorted]

    # See calculate_ea_metrics() for the SIN DATOS rationale of this counter.
    untimed_trades = sum(1 for t in trades_sorted if t["close_time"] is None)

    wins = [p for p in net_pnl_list if p > 0]
    losses = [p for p in net_pnl_list if p <= 0]

    gross_profit = sum(wins)
    gross_loss = sum(losses)
    net_profit = gross_profit + gross_loss

    total_trades = len(net_pnl_list)
    winning_trades = len(wins)
    losing_trades = len(losses)

    win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0.0
    avg_win = (gross_profit / winning_trades) if winning_trades > 0 else 0.0
    avg_loss = (gross_loss / losing_trades) if losing_trades > 0 else 0.0

    profit_factor = (
        (gross_profit / abs(gross_loss))
        if gross_loss != 0
        else (float("inf") if gross_profit > 0 else 0.0)
    )
    payout_ratio = (
        (avg_win / abs(avg_loss))
        if avg_loss != 0
        else (float("inf") if avg_win > 0 else 0.0)
    )
    expectancy = net_profit / total_trades if total_trades > 0 else 0.0

    best_trade = max(net_pnl_list) if net_pnl_list else 0.0
    worst_trade = min(net_pnl_list) if net_pnl_list else 0.0

    long_trades = [t for t in trades_sorted if t["direction"] == "buy"]
    short_trades = [t for t in trades_sorted if t["direction"] == "sell"]
    long_wins = sum(1 for t in long_trades if t["net_pnl"] > 0)
    short_wins = sum(1 for t in short_trades if t["net_pnl"] > 0)

    durations = [
        t["duration_hours"]
        for t in trades_sorted
        if t.get("duration_hours") is not None
    ]
    avg_duration = round(sum(durations) / len(durations), 2) if durations else 0.0

    # Portfolio capital = sum of each EA's capital from config
    mappings = config.get("mappings", {})
    ea_names_in_portfolio = list(
        set(
            t["comment"]
            for t in all_trades
            if t.get("comment") and t["comment"] != "Unknown"
        )
    )
    portfolio_capital = sum(
        float(mappings.get(ea, {}).get("capital", 5000.0))
        for ea in ea_names_in_portfolio
    )
    if portfolio_capital <= 0:
        portfolio_capital = 5000.0  # fallback

    equity_curve = _build_equity_curve(trades_sorted)
    dd_curve = _build_drawdown_curve(equity_curve, portfolio_capital)

    max_dd_dollar, max_dd_pct, last_peak_date = _calc_max_drawdown(
        equity_curve, portfolio_capital
    )
    stagnation_days = _calc_stagnation(last_peak_date)

    ret_dd = (net_profit / max_dd_dollar) if max_dd_dollar > 0 else None

    sqn_val, sqn_note, sqn_label = _calc_sqn(net_pnl_list)
    sharpe = _calc_sharpe(net_pnl_list)
    max_wins, max_losses, avg_wins_streak, avg_losses_streak = _calc_streaks(
        net_pnl_list
    )
    weeks = _weeks_operating(trades_sorted)

    def fmt_pf(val):
        if val == float("inf") or val != val:
            return "∞"
        return round(val, 2)

    return {
        "ea_name": "PORTFOLIO",
        "magic": None,
        "label": "PORTFOLIO",
        "instrument": "—",
        "total_trades": total_trades,
        "winning_trades": winning_trades,
        "losing_trades": losing_trades,
        "long_trades": len(long_trades),
        "short_trades": len(short_trades),
        "long_wins": long_wins,
        "short_wins": short_wins,
        "net_profit": round(net_profit, 2),
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        "best_trade": round(best_trade, 2),
        "worst_trade": round(worst_trade, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "win_rate": round(win_rate, 2),
        "profit_factor": fmt_pf(profit_factor),
        "payout_ratio": fmt_pf(payout_ratio),
        "expectancy": round(expectancy, 2),
        "max_dd_dollar": max_dd_dollar,
        "max_dd_pct": round(max_dd_pct, 2),
        "ret_dd": round(ret_dd, 2) if ret_dd is not None else None,
        "recovery_factor": round(ret_dd, 2) if ret_dd is not None else None,
        "sqn": sqn_val,
        "sqn_note": sqn_note,
        "sqn_label": sqn_label,
        "sharpe_ratio": sharpe,
        "weeks_operating": weeks,
        "avg_duration_hours": avg_duration,
        "stagnation_days": stagnation_days,
        "max_consec_wins": max_wins,
        "max_consec_losses": max_losses,
        "avg_consec_wins": avg_wins_streak,
        "avg_consec_losses": avg_losses_streak,
        "untimed_trades": untimed_trades,
        "equity_curve": equity_curve,
        "drawdown_curve": dd_curve,
        "trades": trades_sorted,
    }


def calculate_all_metrics(parsed_data: dict, config: dict) -> dict:
    """
    Calculate metrics for all EAs and the portfolio.
    Returns {"portfolio": MetricsResult, "by_ea": {ea_name: MetricsResult}}
    Also returns "ea_colors" mapping ea_name -> hex color for charts.
    """
    closed_trades = parsed_data.get("closed_trades", [])
    all_ea_names = parsed_data.get("ea_names", [])

    # Filter to only EAs marked active in config (default True for unmapped EAs)
    mappings = config.get("mappings", {})
    ea_names = [ea for ea in all_ea_names if mappings.get(ea, {}).get("active", True)]

    by_ea = {}
    ea_colors = {}

    for i, ea_name in enumerate(ea_names):
        ea_trades = [t for t in closed_trades if t.get("comment") == ea_name]
        metrics = calculate_ea_metrics(ea_name, ea_trades, config)
        by_ea[ea_name] = metrics

        # Assign color: use the fixed palette first; generate HSL only beyond it.
        # Previous logic used palette for ≤12 EAs and HSL for ALL when >12,
        # discarding the defined colors entirely in the common >12 case.
        if i < len(EA_COLORS):
            ea_colors[ea_name] = EA_COLORS[i]
        else:
            hue = int((i / len(ea_names)) * 360)
            ea_colors[ea_name] = f"hsl({hue}, 70%, 60%)"

    # Portfolio uses only active EA trades
    active_trades = [t for t in closed_trades if t.get("comment") in set(ea_names)]
    portfolio = calculate_portfolio_metrics(active_trades, config)

    return {
        "portfolio": portfolio,
        "by_ea": by_ea,
        "ea_colors": ea_colors,
    }


def _empty_metrics(name, config):
    """Return empty MetricsResult for an EA with no trades."""
    mapping = config.get("mappings", {}).get(name, {})
    magic = mapping.get("magic")
    label = f"{magic} - {name}" if magic else name
    return {
        "ea_name": name,
        "magic": magic,
        "label": label,
        "instrument": mapping.get("instrument", "—"),
        "total_trades": 0,
        "winning_trades": 0,
        "losing_trades": 0,
        "long_trades": 0,
        "short_trades": 0,
        "long_wins": 0,
        "short_wins": 0,
        "net_profit": 0.0,
        "gross_profit": 0.0,
        "gross_loss": 0.0,
        "best_trade": 0.0,
        "worst_trade": 0.0,
        "avg_win": 0.0,
        "avg_loss": 0.0,
        "win_rate": 0.0,
        "profit_factor": 0.0,
        "payout_ratio": 0.0,
        "expectancy": 0.0,
        "max_dd_dollar": 0.0,
        "max_dd_pct": 0.0,
        "ret_dd": None,
        "recovery_factor": None,
        "sqn": None,
        "sqn_note": "",
        "sqn_label": "N/A",
        "sharpe_ratio": None,
        "weeks_operating": 0.0,
        "avg_duration_hours": 0.0,
        "stagnation_days": 0,
        "max_consec_wins": 0,
        "max_consec_losses": 0,
        "avg_consec_wins": 0.0,
        "avg_consec_losses": 0.0,
        "untimed_trades": 0,
        "equity_curve": [],
        "drawdown_curve": [],
        "trades": [],
    }


def format_currency(val):
    """Format as $1,234.56 or -$1,234.56"""
    if val is None:
        return "N/A"
    if isinstance(val, str):
        return val
    sign = "-" if val < 0 else ""
    return f"{sign}${abs(val):,.2f}"


def format_pct(val, decimals=2):
    """Format as percentage string."""
    if val is None:
        return "N/A"
    if isinstance(val, str):
        return val
    return f"{val:.{decimals}f}%"


def format_metric(val, prefix="", suffix="", decimals=2):
    """Generic metric formatter."""
    if val is None:
        return "N/A"
    if isinstance(val, str):
        return val
    return f"{prefix}{val:.{decimals}f}{suffix}"
