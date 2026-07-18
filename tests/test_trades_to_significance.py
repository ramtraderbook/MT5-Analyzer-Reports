"""
test_trades_to_significance.py - Tests for the 3B "how many more trades until
the verdict is trustworthy" signal (incubation_validator.trades_to_winrate_significance).

The win-rate gate is a one-sided lower binomial test. The function projects the
current observed rate forward and returns the additional trades until the
left-tail p-value crosses alpha. The p-value is cross-checked here against
scipy.stats.binom.cdf -- an independent implementation, NOT the module's own
math.comb path -- so the crossing point is verified, not merely self-consistent.
"""

import pytest

import incubation_validator as iv

scipy_stats = pytest.importorskip("scipy.stats")


def _pval(wins, n, p0):
    """Independent left-tail P(X <= wins) via scipy, the oracle."""
    return float(scipy_stats.binom.cdf(wins, n, p0))


ALPHA = 0.03


# ── not-applicable cases return None ─────────────────────────────────────────


@pytest.mark.parametrize("wins,n,bt_wr", [
    (0, 0, 50.0),     # no trades yet
    (5, 10, None),    # no backtest win rate
    (5, 10, 0.0),     # non-positive backtest rate
])
def test_returns_none_when_not_applicable(wins, n, bt_wr):
    assert iv.trades_to_winrate_significance(wins, n, bt_wr) is None


def test_returns_none_when_meeting_or_beating_backtest():
    # observed 55% >= backtest 50% -> a lower-tail kill can never be significant
    assert iv.trades_to_winrate_significance(55, 100, 50.0) is None
    # exactly at the backtest rate -> still no pending kill
    assert iv.trades_to_winrate_significance(50, 100, 50.0) is None


# ── already significant returns 0 ────────────────────────────────────────────


def test_returns_zero_when_gate_already_fires():
    # 20 wins in 100 vs a 50% backtest: wildly below, p-value ~0 << alpha
    assert _pval(20, 100, 0.50) < ALPHA
    assert iv.trades_to_winrate_significance(20, 100, 50.0) == 0


# ── the crossing point is exactly the first significant projection ───────────


@pytest.mark.parametrize("wins,n,bt_wr", [
    (13, 30, 55.0),   # 43% vs 55%, not yet significant (cdf 0.14)
    (18, 40, 55.0),   # 45% vs 55%, not yet significant (cdf 0.13)
    (24, 50, 55.0),   # 48% vs 55%, not yet significant (cdf 0.20)
])
def test_crossing_point_is_the_first_significant_projection(wins, n, bt_wr):
    p0 = bt_wr / 100.0
    p_hat = wins / n
    assert p_hat < p0  # precondition: these are underperformers
    assert _pval(wins, n, p0) >= ALPHA  # and not yet significant now

    extra = iv.trades_to_winrate_significance(wins, n, bt_wr, alpha=ALPHA)
    assert isinstance(extra, int) and extra > 0

    # At the returned horizon the projected p-value is significant (oracle)...
    n_hit = n + extra
    wins_hit = round(p_hat * n_hit)
    assert _pval(wins_hit, n_hit, p0) < ALPHA

    # ...and one trade earlier it was not: this is genuinely the FIRST crossing.
    n_prev = n + extra - 1
    wins_prev = round(p_hat * n_prev)
    assert _pval(wins_prev, n_prev, p0) >= ALPHA


def test_all_losses_matches_closed_form():
    # wins=0 -> p-value is (1-p0)^n; find smallest N with (1-p0)^N < alpha.
    # p0=0.5 -> 0.5^N < 0.03 -> N >= 6 (0.5^5=0.03125 >= 0.03, 0.5^6=0.0156 < 0.03)
    # starting from n=1 (already 0.5 >= alpha), need N=6 total -> extra=5.
    extra = iv.trades_to_winrate_significance(0, 1, 50.0, alpha=0.03)
    assert extra == 5
    assert _pval(0, 6, 0.5) < 0.03 and _pval(0, 5, 0.5) >= 0.03


def test_worse_rate_needs_fewer_trades_than_mild_underperformance():
    # A deep underperformer reaches a firm kill sooner than a marginal one.
    deep = iv.trades_to_winrate_significance(40, 100, 60.0)   # 40% vs 60%
    mild = iv.trades_to_winrate_significance(48, 100, 50.0)   # 48% vs 50%
    assert isinstance(deep, int)
    # mild may be far out (None) or a large number; either way deep < it
    if mild is None:
        assert True
    else:
        assert deep < mild


def test_marginal_underperformance_beyond_horizon_returns_none():
    # 49.5%-ish vs 50%: significance is many hundreds of trades away -> None
    assert iv.trades_to_winrate_significance(199, 400, 50.0, max_extra=200) is None


# ── wiring into the verdict card (build_verdict_card) ────────────────────────

import incubation_domain as dom


def _eval(verdict, wins=13, n=30, bt=55.0, gate=True):
    e = {
        "verdict": verdict,
        "current_checkpoint": "CP2",
        "score": None,
        "days_incubating": 30,
        "details": {"gates": {}},
    }
    if gate:
        e["details"]["gates"]["win_rate_binomial"] = {
            "passed": True, "wins": wins, "n": n, "bt_wr": bt,
        }
    return e


def test_verdict_card_surfaces_trades_to_call_for_underperformer():
    # 13/30 = 43% vs 55% BT, not yet significant -> a positive "wait" advisory
    card = dom.build_verdict_card(_eval("CONTINUAR"))
    assert card["trades_to_call"] == iv.trades_to_winrate_significance(13, 30, 55.0)
    assert card["trades_to_call"] > 0


def test_verdict_card_hides_trades_to_call_when_not_actionable():
    # already ELIMINAR -> the kill happened, no "wait" advisory
    assert dom.build_verdict_card(_eval("ELIMINAR"))["trades_to_call"] is None
    # meeting/beating backtest (17/30 = 57% >= 55%) -> no pending kill
    assert dom.build_verdict_card(_eval("CONTINUAR", wins=17))["trades_to_call"] is None
    # no win-rate gate present -> nothing to compute
    assert dom.build_verdict_card(_eval("CONTINUAR", gate=False))["trades_to_call"] is None
