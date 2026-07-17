"""
test_char_metrics.py - Caracterizacion de metrics.py (capa 1, harness P-A).

Fija el comportamiento ACTUAL de calculate_ea_metrics y
calculate_portfolio_metrics -- lo que HAY, no lo que DEBERIA haber. Los
defectos conocidos se marcan con DEFECT-PIN. Todas las entradas son
hardcodeadas via los helpers de conftest.py; nada se lee de disco.

calculate_portfolio_metrics tenia CERO cobertura en este repo antes de este
archivo -- es la brecha real que se cierra aqui, ademas de la caracterizacion
de calculate_ea_metrics.
"""

import pytest

import metrics as m
from conftest import make_trade, make_trades, make_config


EXPECTED_41_KEYS = {
    "ea_name", "magic", "label", "instrument",
    "total_trades", "winning_trades", "losing_trades",
    "long_trades", "short_trades", "long_wins", "short_wins",
    "net_profit", "gross_profit", "gross_loss",
    "best_trade", "worst_trade",
    "avg_win", "avg_loss", "win_rate",
    "profit_factor", "payout_ratio", "expectancy",
    "max_dd_dollar", "max_dd_pct",
    "ret_dd", "recovery_factor",
    "sqn", "sqn_note", "sqn_label",
    "sharpe_ratio",
    "weeks_operating", "avg_duration_hours", "stagnation_days",
    "max_consec_wins", "max_consec_losses",
    "avg_consec_wins", "avg_consec_losses",
    "untimed_trades",
    "equity_curve", "drawdown_curve", "trades",
}


def _raw_trade(net_pnl, close_time=None, direction="buy", symbol="EURUSD",
                duration_hours=1.0, comment="MyEA", position_id=1):
    """Trade con close_time REALMENTE None (make_trade() de conftest sustituye
    None por una fecha por defecto -- para pinnear el camino de trades
    genuinamente sin fecha hace falta construir el dict a mano)."""
    return {
        "position_id": position_id, "symbol": symbol, "direction": direction,
        "close_time": close_time, "net_pnl": float(net_pnl),
        "duration_hours": duration_hours, "comment": comment,
    }


# ── §1.4: contrato de 41 claves -- ea_metrics, portfolio, _empty_metrics ───

def test_calculate_ea_metrics_41_key_contract():
    res = m.calculate_ea_metrics("MyEA", make_trades([100.0, -50.0, 200.0]), make_config())
    assert set(res.keys()) == EXPECTED_41_KEYS


def test_calculate_portfolio_metrics_same_41_key_contract():
    res = m.calculate_portfolio_metrics(make_trades([100.0, -50.0]), make_config())
    assert set(res.keys()) == EXPECTED_41_KEYS
    assert res["ea_name"] == "PORTFOLIO"
    assert res["magic"] is None
    assert res["label"] == "PORTFOLIO"
    assert res["instrument"] == "—"


def test_empty_metrics_same_41_key_contract_for_ea_and_portfolio():
    empty_ea = m._empty_metrics("MyEA", make_config())
    assert set(empty_ea.keys()) == EXPECTED_41_KEYS
    assert m.calculate_ea_metrics("MyEA", [], make_config()) == empty_ea

    empty_portfolio = m.calculate_portfolio_metrics([], {})
    assert set(empty_portfolio.keys()) == EXPECTED_41_KEYS
    assert empty_portfolio["ea_name"] == "PORTFOLIO"
    assert empty_portfolio["total_trades"] == 0


# ── §7: net_pnl==0 cuenta como LOSS ─────────────────────────────────────────

def test_net_pnl_zero_counts_as_loss_ea_level():
    trades = [make_trade(0.0), make_trade(10.0)]
    res = m.calculate_ea_metrics("MyEA", trades, make_config())
    assert res["losing_trades"] == 1
    assert res["winning_trades"] == 1
    assert res["win_rate"] == 50.0


def test_net_pnl_zero_counts_as_loss_portfolio_level():
    trades = [make_trade(0.0, comment="EA1"), make_trade(10.0, comment="EA1")]
    res = m.calculate_portfolio_metrics(trades, make_config(ea_name="EA1"))
    assert res["losing_trades"] == 1
    assert res["winning_trades"] == 1


def test_payout_ratio_zero_pnl_trade_inflation_defect_pin():
    # DEFECT-PIN: net_pnl==0 cuenta como perdida (metrics.py:354 `p <= 0`),
    # lo que infla artificialmente losing_trades y por lo tanto ENCOGE
    # |avg_loss| (denominador mas grande, mismo numerador) -- inflando a su
    # vez payout_ratio = avg_win/|avg_loss| (metrics.py:366/373-377).
    # Pinned because it is current behavior, NOT because it is correct.
    #
    # trades: 1 ganador (100.0), 2 perdedores reales (-50.0, -30.0), 2
    # trades en net_pnl==0.0 exactos.
    trades = make_trades([100.0, 0.0, -50.0, -30.0, 0.0])
    res = m.calculate_ea_metrics("MyEA", trades, make_config(capital=10000.0))

    assert res["winning_trades"] == 1
    assert res["losing_trades"] == 4  # 2 perdedores reales + 2 en net_pnl==0.0
    assert res["avg_win"] == 100.0
    # avg_loss = gross_loss / losing_trades = -80.0 / 4 = -20.0 (produccion)
    assert res["avg_loss"] == -20.0
    # payout_ratio reportado = avg_win / |avg_loss| = 100 / 20 = 5.0
    assert res["payout_ratio"] == 5.0

    # Textbook (particion ESTRICTA < 0, sin los dos trades en 0): avg_loss
    # real = -80.0 / 2 = -40.0, payout_ratio real = 100 / 40 = 2.5 -- el
    # valor reportado (5.0) es exactamente 2x el valor textbook (2.5).
    textbook_avg_loss = -80.0 / 2
    textbook_payout = 100.0 / abs(textbook_avg_loss)
    assert textbook_avg_loss == -40.0
    assert textbook_payout == 2.5
    assert res["payout_ratio"] == pytest.approx(textbook_payout * 2, rel=1e-9)


# ── §8: profit_factor / payout_ratio -- union type float|"∞" ───────────────

def test_profit_factor_and_payout_ratio_are_infinity_string_when_no_losses():
    trades = make_trades([100.0, 50.0, 30.0])  # todas ganadoras
    res = m.calculate_ea_metrics("MyEA", trades, make_config())
    assert res["profit_factor"] == "∞"
    assert isinstance(res["profit_factor"], str)
    assert res["payout_ratio"] == "∞"
    assert isinstance(res["payout_ratio"], str)


def test_profit_factor_and_payout_ratio_are_float_when_mixed():
    trades = make_trades([100.0, -50.0])
    res = m.calculate_ea_metrics("MyEA", trades, make_config())
    assert res["profit_factor"] == 2.0
    assert isinstance(res["profit_factor"], float)


def test_empty_metrics_reports_zero_float_not_infinity_string():
    """
    _empty_metrics usa 0.0 (float) para profit_factor/payout_ratio -- no
    "∞" -- una TERCERA representacion del "no hay datos" distinta de la que
    usa calculate_ea_metrics con trades reales pero sin perdidas.
    """
    empty = m._empty_metrics("MyEA", make_config())
    assert empty["profit_factor"] == 0.0
    assert isinstance(empty["profit_factor"], float)
    assert empty["payout_ratio"] == 0.0
    assert empty["sqn_note"] == ""  # distinto de "(insuficientes datos)" de _calc_sqn


# ── §7: max_dd desde baseline cero + tracking independiente $/% ───────────

def test_max_drawdown_baseline_is_zero_not_first_equity_point():
    """peak_pnl arranca en 0.0, no en equity[0] (metrics.py:134)."""
    trades = make_trades([-500.0, 100.0])  # arranca perdiendo, nunca hay pico positivo
    res = m.calculate_ea_metrics("MyEA", trades, make_config(capital=10000.0))
    # el DD se mide desde 0, no desde el primer punto de equity (-500)
    assert res["max_dd_dollar"] == 500.0
    assert res["max_dd_pct"] == pytest.approx(500 / 10000 * 100, rel=1e-6)


def test_max_drawdown_dollar_and_pct_tracked_independently():
    """
    Ejemplo de docs/metrics-formulas.md: capital=5000, pnls=[5000,-1000,6000,-1010].
    El peor DD en DOLARES ocurre en el ultimo punto (1010, desde el pico de
    10000); el peor DD en PORCENTAJE ocurre en el segundo punto (10.0%,
    desde el pico de 5000, denominador mas chico) -- momentos DISTINTOS.
    """
    trades = make_trades([5000.0, -1000.0, 6000.0, -1010.0])
    res = m.calculate_ea_metrics("MyEA", trades, make_config(capital=5000.0))
    assert res["max_dd_dollar"] == 1010.0
    assert res["max_dd_pct"] == 10.0


def test_max_drawdown_double_round_4dp_then_2dp():
    """
    _calc_max_drawdown redondea max_dd_pct a 4dp (metrics.py:159);
    calculate_ea_metrics vuelve a redondear ese valor a 2dp
    (metrics.py:460) -- doble redondeo verificado end-to-end.
    """
    trades = make_trades([1000.0, -2000.0, 500.0])
    res = m.calculate_ea_metrics("MyEA", trades, make_config(capital=10000.0))
    # dd_dollar=2000 sobre peak_abs=11000 -> 18.181818...% -> 4dp=18.1818 -> 2dp=18.18
    assert res["max_dd_dollar"] == 2000.0
    assert res["max_dd_pct"] == 18.18
    assert res["drawdown_curve"][-2]["dd_pct"] == -18.1818  # el propio 4dp intermedio


# ── §8 DEFECT-PIN: all-untimed -> curva vacia -> DD 0.0 con perdidas reales ──

def test_all_untimed_trades_yield_empty_equity_curve_and_zero_dd_defect():
    """
    DEFECT-PIN: si TODOS los trades tienen close_time=None,
    _build_equity_curve devuelve [] (metrics.py:74-76), y
    _calc_max_drawdown([], capital) devuelve (0.0, 0.0, None)
    (metrics.py:131-132). El resultado es que un EA con perdidas reales y
    documentadas en net_profit reporta max_dd_dollar=0.0 y max_dd_pct=0.0 --
    el peor drawdown posible queda invisible porque ningun trade tenia
    fecha. Pinneado porque es el comportamiento actual
    (metrics.py:74-76, 131-132; known-issues.md §7), NO porque sea correcto.
    """
    trades = [_raw_trade(-500.0, position_id=i) for i in range(5)]
    res = m.calculate_ea_metrics("MyEA", trades, make_config())
    assert res["net_profit"] == -2500.0  # la perdida SI se contabiliza...
    assert res["untimed_trades"] == 5
    assert res["equity_curve"] == []
    assert res["drawdown_curve"] == []
    assert res["max_dd_dollar"] == 0.0  # ...pero el DD queda en 0 pese a la perdida real
    assert res["max_dd_pct"] == 0.0


# ── §8 DEFECT-PIN: capital<=0 silencia todo el DD% (no el $) ──────────────

def test_capital_zero_silently_zeroes_dd_pct_but_not_dd_dollar_defect():
    """
    DEFECT-PIN: con capital<=0 y una serie SIN picos positivos de P&L
    (peak_pnl se queda en 0.0 durante todo el recorrido), peak_abs =
    capital + peak_pnl = capital <= 0 en cada punto -> dd_pct se fuerza a
    0.0 en TODOS los puntos (metrics.py:118, 155), aunque max_dd_dollar SI
    sigue reflejando la perdida real en dolares. Pinneado porque es el
    comportamiento actual, NO porque sea correcto -- un capital mal
    configurado (0 o negativo) hace desaparecer silenciosamente el DD% de
    la UI sin ningun error.
    """
    trades = make_trades([-100.0, -200.0, -50.0])  # nunca hay pnl positivo
    res = m.calculate_ea_metrics("MyEA", trades, make_config(capital=0.0))
    assert res["max_dd_dollar"] == 350.0  # el dolar SI es real
    assert res["max_dd_pct"] == 0.0       # pero el % queda silenciosamente en 0

    res_neg = m.calculate_ea_metrics("MyEA", trades, make_config(capital=-500.0))
    assert res_neg["max_dd_dollar"] == 350.0
    assert res_neg["max_dd_pct"] == 0.0


# ── §8 DEFECT-PIN: fecha de pico malformada -> 0 dias de estancamiento ─────

def test_malformed_peak_date_returns_zero_stagnation_defect():
    """
    DEFECT-PIN: _calc_stagnation atrapa (ValueError, TypeError) alrededor de
    date.fromisoformat() y devuelve 0 -- el MEJOR valor posible -- en vez de
    None o de propagar el error (metrics.py:162-170). Una fecha de pico
    corrupta se disfraza silenciosamente de "cero dias sin nuevo maximo".
    Pinneado porque es el comportamiento actual, NO porque sea correcto.
    """
    assert m._calc_stagnation("not-a-date") == 0
    assert m._calc_stagnation("2026-13-45") == 0  # mes/dia invalidos
    assert m._calc_stagnation(None) == 0
    assert m._calc_stagnation("") == 0
    # contraste: una fecha valida SI calcula dias reales contra el reloj congelado
    assert m._calc_stagnation("2026-07-01") == 15


# ── SQN / Sharpe: guardias de MIN_COEFFICIENT_OF_VARIATION y N<20 ─────────

def test_sqn_withholds_label_below_min_trades_but_reports_value():
    """n<MIN_TRADES_FOR_SQN_LABEL(20): se reporta sqn con nota
    "(orientativo)" pero sqn_label queda en "N/A" -- la muestra no alcanza
    para sostener una etiqueta de calidad."""
    trades = make_trades([5.0, 6.0, -1.0, 4.0, 3.0])
    res = m.calculate_ea_metrics("MyEA", trades, make_config())
    assert res["sqn"] is not None
    assert res["sqn_note"] == "(orientativo)"
    assert res["sqn_label"] == "N/A"


def test_sqn_and_sharpe_none_when_coefficient_of_variation_too_low():
    """serie con std/mean por debajo de MIN_COEFFICIENT_OF_VARIATION(0.01)
    -- ni SQN ni Sharpe son estimables."""
    trades = make_trades([100.0] * 25)  # PnL identico -> std=0
    res = m.calculate_ea_metrics("MyEA", trades, make_config())
    assert res["sqn"] is None
    assert res["sqn_note"] == "(desviación cero)"
    assert res["sqn_label"] == "N/A"
    assert res["sharpe_ratio"] is None


def test_sqn_insufficient_data_below_2_trades():
    trades = make_trades([100.0])
    res = m.calculate_ea_metrics("MyEA", trades, make_config())
    assert res["sqn"] is None
    assert res["sqn_note"] == "(insuficientes datos)"
    assert res["sharpe_ratio"] is None


# ── weeks_operating: clamp a 0.0 (nunca negativo) ──────────────────────────

def test_weeks_operating_clamped_to_zero_for_same_day_intraday_trade():
    """Reloj congelado en 2026-07-16 12:00; un trade cerrado hoy a una hora
    posterior a medianoche produciria un delta negativo sin el clamp
    (metrics.py:317-322)."""
    from datetime import datetime as real_datetime
    trades = [make_trade(100.0, close_time=real_datetime(2026, 7, 16, 23, 0, 0))]
    res = m.calculate_ea_metrics("MyEA", trades, make_config())
    assert res["weeks_operating"] == 0.0


# ── calculate_portfolio_metrics: capital = suma de capitales por EA activa ──

def test_portfolio_capital_is_sum_of_per_ea_capital_from_comment_field():
    t1 = make_trades([100.0, -50.0], comment="EA1")
    t2 = make_trades([200.0, -30.0], comment="EA2")
    config = {"mappings": {"EA1": {"capital": 3000.0}, "EA2": {"capital": 2000.0}}}
    res = m.calculate_portfolio_metrics(t1 + t2, config)
    assert res["total_trades"] == 4
    # capital efectivo = 3000+2000 = 5000: verificado indirectamente via DD%
    # contra un calculo equivalente de un solo EA con ese mismo capital.
    single = m.calculate_ea_metrics("X", t1 + t2, {"mappings": {"X": {"capital": 5000.0}}})
    assert res["max_dd_pct"] == single["max_dd_pct"]
    assert res["max_dd_dollar"] == single["max_dd_dollar"]


def test_portfolio_capital_fallback_to_5000_when_sum_is_non_positive():
    """
    DEFECT-PIN adyacente: si el capital configurado de las EAs presentes en
    el portfolio suma <=0, cae a un fallback fijo de 5000.0
    (metrics.py:558-559) -- comparte la misma familia de comportamiento
    silencioso que el capital<=0 a nivel EA, pero aqui hay un fallback en
    vez de un cero silencioso.
    """
    trades = make_trades([-100.0, -200.0], comment="EA3")
    config = {"mappings": {"EA3": {"capital": -10.0}}}
    res = m.calculate_portfolio_metrics(trades, config)
    equivalent = m.calculate_ea_metrics("EA3", trades, {"mappings": {"EA3": {"capital": 5000.0}}})
    assert res["max_dd_pct"] == equivalent["max_dd_pct"]
    assert res["max_dd_dollar"] == equivalent["max_dd_dollar"]


def test_portfolio_metrics_no_config_defaults_to_empty_dict():
    """config=None (default del parametro) se normaliza a {} internamente
    (metrics.py:486-487) -- no crashea por AttributeError."""
    trades = make_trades([100.0, -50.0], comment="EA1")
    res = m.calculate_portfolio_metrics(trades, config=None)
    assert res["total_trades"] == 2


def test_portfolio_metrics_infinity_union_type_same_as_ea_level():
    trades = make_trades([100.0, 50.0], comment="EA1")  # todas ganadoras
    res = m.calculate_portfolio_metrics(trades, make_config(ea_name="EA1"))
    assert res["profit_factor"] == "∞"
    assert res["payout_ratio"] == "∞"


def test_portfolio_metrics_excludes_trades_with_unknown_or_missing_comment():
    """
    calculate_portfolio_metrics NO filtra por comment al construir
    all_trades (eso lo hace el llamador via calculate_all_metrics) -- pero
    SI filtra por comment al sumar el capital de las EAs presentes
    (metrics.py:547-553): "Unknown" y comment ausente/falsy quedan fuera de
    la suma de capital, cayendo en el fallback de 5000 si son las unicas
    EAs presentes.
    """
    trades = make_trades([100.0, -400.0], comment="Unknown")
    res = m.calculate_portfolio_metrics(trades, {"mappings": {"Unknown": {"capital": 999999.0}}})
    equivalent = m.calculate_ea_metrics("X", trades, {"mappings": {"X": {"capital": 5000.0}}})
    assert res["max_dd_pct"] == equivalent["max_dd_pct"]  # capital=999999 fue IGNORADO


# ── Formulas basicas (verbatim contra metrics.py:364-378) ─────────────────

def test_win_rate_profit_factor_payout_expectancy_formulas():
    trades = make_trades([100.0, 100.0, -50.0, -50.0])  # 2 wins, 2 losses
    res = m.calculate_ea_metrics("MyEA", trades, make_config())
    assert res["win_rate"] == 50.0
    assert res["profit_factor"] == 2.0    # gross_profit(200)/abs(gross_loss)(100)
    assert res["payout_ratio"] == 2.0     # avg_win(100)/abs(avg_loss)(50)
    assert res["expectancy"] == 25.0      # net_profit(100)/total_trades(4)


def test_streaks_max_and_avg():
    # win,win,win,loss,loss,win  (close_time order == list order via make_trades)
    trades = make_trades([10.0, 10.0, 10.0, -5.0, -5.0, 10.0])
    res = m.calculate_ea_metrics("MyEA", trades, make_config())
    assert res["max_consec_wins"] == 3
    assert res["max_consec_losses"] == 2
    assert res["avg_consec_wins"] == pytest.approx((3 + 1) / 2, rel=1e-6)
    assert res["avg_consec_losses"] == 2.0
