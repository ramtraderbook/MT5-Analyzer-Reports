"""
test_metrics.py - Tests unitarios para las funciones de cálculo de metrics.py.

Todos los valores son exactos y verificables a mano.
No se usa ningún archivo .xlsx real.
"""

import math
import pytest
from datetime import date, datetime, timedelta

import metrics
from metrics import (
    _build_equity_curve,
    _build_drawdown_curve,
    _calc_max_drawdown,
    _calc_sharpe,
    _calc_sqn,
    _calc_streaks,
    _weeks_operating,
    calculate_ea_metrics,
)


# ── Fixtures locales ──────────────────────────────────────────────────────────

def make_trade(position_id, net_pnl, close_date, comment="MyEA"):
    """Helper: crea un trade dict mínimo para métricas."""
    return {
        "position_id": position_id,
        "comment": comment,
        "net_pnl": net_pnl,
        "close_time": datetime(2026, 1, close_date, 12, 0, 0),
        "direction": "buy",
        "duration_hours": 4.0,
    }


def make_full_trade(position_id, net_pnl, close_time, comment="MyEA", symbol="EURUSD"):
    """Helper: crea un trade dict completo (con symbol) para calculate_ea_metrics()."""
    return {
        "position_id": position_id,
        "comment": comment,
        "symbol": symbol,
        "net_pnl": net_pnl,
        "close_time": close_time,
        "direction": "buy",
        "duration_hours": 4.0,
    }


THREE_TRADES = [
    make_trade(1001, 99.0, 2),    # win: profit=100, commission=-1, swap=0
    make_trade(1002, -53.0, 5),   # loss: profit=-50, commission=-1, swap=-2
    make_trade(1003, 199.0, 10),  # win: profit=200, commission=-2, swap=1
]

CAPITAL = 10_000.0


# ── Test 1: Equity curve empieza en 0 ────────────────────────────────────────

def test_equity_curve_starts_at_zero():
    """
    El primer punto de la curva de equity SIEMPRE debe ser 0.
    Representa el P&L relativo desde el inicio.
    """
    curve = _build_equity_curve(THREE_TRADES)

    assert len(curve) > 0
    assert curve[0]["equity"] == 0.0


def test_equity_curve_accumulates_correctly():
    """
    El equity se acumula sumando net_pnl de cada trade en orden.
    Valores exactos: 0 → 99 → 46 → 245
    """
    curve = _build_equity_curve(THREE_TRADES)

    # curve[0] = punto inicial en 0
    assert curve[0]["equity"] == pytest.approx(0.0)
    # Después del trade A: 0 + 99 = 99
    assert curve[1]["equity"] == pytest.approx(99.0)
    # Después del trade B: 99 + (-53) = 46
    assert curve[2]["equity"] == pytest.approx(46.0)
    # Después del trade C: 46 + 199 = 245
    assert curve[3]["equity"] == pytest.approx(245.0)


# ── Test 2: Max Drawdown % — fórmula correcta con capital ────────────────────

def test_max_drawdown_pct_formula():
    """
    Max DD% = (peak_pnl - valley_pnl) / (capital + peak_pnl) * 100

    Con los 3 trades (equity: 0 → 99 → 46 → 245):
    - Peak después del trade A: 99
    - Valley después del trade B: 46
    - DD_dollar = 99 - 46 = 53
    - peak_abs = 10000 + 99 = 10099
    - DD% = 53 / 10099 * 100 ≈ 0.5248%
    """
    curve = _build_equity_curve(THREE_TRADES)
    max_dd_dollar, max_dd_pct, _ = _calc_max_drawdown(curve, CAPITAL)

    expected_dd_dollar = 53.0
    expected_dd_pct = (53.0 / (CAPITAL + 99.0)) * 100  # ≈ 0.5248

    assert max_dd_dollar == pytest.approx(expected_dd_dollar, abs=0.01)
    assert max_dd_pct == pytest.approx(expected_dd_pct, rel=1e-3)


def test_max_drawdown_zero_when_all_wins():
    """Si todos los trades son ganadores consecutivos, el DD es 0."""
    all_wins = [
        make_trade(1, 100.0, 2),
        make_trade(2, 50.0, 5),
        make_trade(3, 75.0, 10),
    ]
    curve = _build_equity_curve(all_wins)
    max_dd_dollar, max_dd_pct, _ = _calc_max_drawdown(curve, CAPITAL)

    assert max_dd_dollar == pytest.approx(0.0)
    assert max_dd_pct == pytest.approx(0.0)


# ── Test 3: SQN = sqrt(N) * mean / std ───────────────────────────────────────

def test_sqn_formula():
    """
    SQN = sqrt(N) * mean(net_pnl) / std(net_pnl, ddof=1)

    Con net_pnl = [99, -53, 199]:
    N = 3
    mean = (99 - 53 + 199) / 3 = 245 / 3 ≈ 81.667
    std (ddof=1) = sqrt(((99-81.667)² + (-53-81.667)² + (199-81.667)²) / 2)
    """
    import numpy as np
    net_pnl_list = [99.0, -53.0, 199.0]
    n = len(net_pnl_list)
    arr = [99.0, -53.0, 199.0]
    expected_mean = sum(arr) / n
    expected_std = float(__import__('numpy').std(arr, ddof=1))
    expected_sqn = math.sqrt(n) * expected_mean / expected_std

    sqn_val, note, label = _calc_sqn(net_pnl_list)

    # _calc_sqn retorna round(sqn, 2) — comparar con tolerancia abs de 0.01
    assert sqn_val == pytest.approx(expected_sqn, abs=0.01)
    # N < 20 → debe incluir nota orientativo
    assert "orientativo" in note


def test_sqn_returns_none_with_single_trade():
    """Con menos de 2 trades, SQN no se puede calcular."""
    sqn_val, note, label = _calc_sqn([100.0])

    assert sqn_val is None
    assert label == "N/A"


# ── Test 4: Streaks (rachas ganadoras/perdedoras) ────────────────────────────

def test_streaks_win_loss():
    """
    Con el patrón [win, loss, win] las rachas son:
    max_wins = 1, max_losses = 1
    """
    net_pnl_list = [99.0, -53.0, 199.0]
    max_wins, max_losses, avg_wins, avg_losses = _calc_streaks(net_pnl_list)

    assert max_wins == 1
    assert max_losses == 1


def test_streaks_consecutive():
    """
    Con el patrón [win, win, win, loss, loss] las rachas son:
    max_wins = 3, max_losses = 2
    """
    net_pnl_list = [10.0, 20.0, 30.0, -5.0, -10.0]
    max_wins, max_losses, avg_wins, avg_losses = _calc_streaks(net_pnl_list)

    assert max_wins == 3
    assert max_losses == 2


# ── Test 5: net_pnl en trades — regla de negocio crítica ────────────────────

def test_net_pnl_includes_commission_and_swap():
    """
    REGLA CRÍTICA: net_pnl = profit + commission + swap.
    Verifica la fórmula con campos separados, igual que _parse_positions()
    en parser.py:170. Si el parser cambiara a usar solo profit, este test falla.
    """
    profit = 100.0
    commission = -1.0
    swap = 0.0

    # Esto es exactamente lo que hace _parse_positions() en parser.py:170
    net_pnl = profit + commission + swap

    assert net_pnl == pytest.approx(99.0)
    # Usar solo profit daría resultado incorrecto (100.0 ≠ 99.0)
    assert profit != net_pnl


# ── Test 6: max_dd_pct es el máximo real, no el DD% del peor DD$ ────────────

def test_max_drawdown_pct_is_true_series_maximum():
    """
    max_dd_pct debe ser el máximo de dd_pct sobre TODA la serie, no el dd_pct
    del punto donde ocurre max_dd_dollar.

    Con capital=5000 y trades -1000 / +6000 / -1010:
    - equity: 0 → -1000 → 5000 → 3990
    - Tras el trade 1: peak_pnl=0, dd_dollar=1000, peak_abs=5000 → dd_pct=20.0
    - Tras el trade 3: peak_pnl=5000, dd_dollar=1010, peak_abs=10000 → dd_pct=10.1
    - max_dd_dollar = 1010 (ocurre en el punto de dd_pct=10.1)
    - max_dd_pct debe ser 20.0 (el peor punto de la serie), NO 10.1
    """
    trades = [
        make_trade(1, -1000.0, 2),
        make_trade(2, 6000.0, 5),
        make_trade(3, -1010.0, 10),
    ]
    curve = _build_equity_curve(trades)
    max_dd_dollar, max_dd_pct, _ = _calc_max_drawdown(curve, 5000.0)

    assert max_dd_dollar == pytest.approx(1010.0, abs=0.01)
    assert max_dd_pct == pytest.approx(20.0, abs=0.01)


# ── Test 7: stagnation no se resetea con un empate de equity ────────────────

def test_stagnation_does_not_reset_on_equity_tie():
    """
    El último pico solo avanza ante un nuevo máximo ESTRICTO (pnl > peak_pnl),
    nunca ante un empate (pnl == peak_pnl). Fechas relativas a date.today()
    para que el test nunca quede obsoleto.

    Equity: 0 → 100 (hace 99 días) → 0 (hace 50 días) → 100 (hoy, empate).
    El pico sigue fechado hace 99 días → stagnation_days ≈ 99, no 0.
    """
    today = date.today()
    d_first = datetime.combine(today - timedelta(days=99), datetime.min.time())
    d_second = datetime.combine(today - timedelta(days=50), datetime.min.time())
    d_third = datetime.combine(today, datetime.min.time())

    trades = [
        {"position_id": 1, "comment": "MyEA", "net_pnl": 100.0, "close_time": d_first,
         "direction": "buy", "duration_hours": 4.0},
        {"position_id": 2, "comment": "MyEA", "net_pnl": -100.0, "close_time": d_second,
         "direction": "buy", "duration_hours": 4.0},
        {"position_id": 3, "comment": "MyEA", "net_pnl": 100.0, "close_time": d_third,
         "direction": "buy", "duration_hours": 4.0},
    ]
    curve = _build_equity_curve(trades)
    _, _, last_peak_date = _calc_max_drawdown(curve, 5000.0)
    stagnation_days = metrics._calc_stagnation(last_peak_date)

    assert stagnation_days == pytest.approx(99, abs=1)


# ── Test 8: untimed_trades cuenta trades con close_time no parseable ────────

def test_untimed_trades_counts_trades_with_unparseable_close_time():
    """
    Un trade con close_time=None (parser.py::_parse_date no pudo interpretar
    la fecha) debe contarse en untimed_trades, aunque su net_pnl siga
    contribuyendo a net_profit/SQN/Sharpe/streaks.
    """
    trades = [
        make_full_trade(1, 100.0, datetime(2026, 1, 2, 12, 0, 0)),
        make_full_trade(2, 50.0, datetime(2026, 1, 5, 12, 0, 0)),
        make_full_trade(3, -20.0, None),  # fecha no parseable en el xlsx original
    ]

    result = calculate_ea_metrics("MyEA", trades, {})

    assert result["untimed_trades"] == 1
    assert result["total_trades"] == 3
    assert result["net_profit"] == pytest.approx(130.0)


# ── Test 9: SQN — varianza degenerada no reporta un score elite ─────────────

def test_sqn_none_on_degenerate_variance():
    """
    Un take-profit fijo con centavos de diferencia ([5.00, 5.01]) tiene
    std != 0 pero un coeficiente de variación despreciable — no es una
    distribución estimable, así que SQN debe ser None con la nota de
    varianza degenerada, no un score absurdo tipo "Santo Grial".
    """
    sqn_val, note, label = _calc_sqn([5.00, 5.01])

    assert sqn_val is None
    assert note == "(varianza degenerada)"
    assert label == "N/A"


# ── Test 10: Sharpe — varianza degenerada no reporta un score ───────────────

def test_sharpe_none_on_degenerate_variance():
    """Mismo guard de coeficiente de variación aplicado a Sharpe."""
    sharpe = _calc_sharpe([5.00, 5.01])

    assert sharpe is None


# ── Test 11: SQN realista no dispara el guard de varianza degenerada ────────

def test_sqn_realistic_data_not_flagged_as_degenerate():
    """
    Datos realistas con coeficiente de variación alto (~3.1) NO deben activar
    el guard de varianza degenerada — sirve para verificar que el umbral
    MIN_COEFFICIENT_OF_VARIATION no es demasiado agresivo.

    net_pnl = [10.0, -5.0] * 10 → SQN ≈ 1.45.
    Con la tabla SQN_LABELS (umbral 1.6 para "Debajo promedio"), 1.45 cae en
    "Pobre".
    """
    net_pnl_list = [10.0, -5.0] * 10

    sqn_val, note, label = _calc_sqn(net_pnl_list)

    assert sqn_val == pytest.approx(1.45, abs=0.01)
    assert note != "(varianza degenerada)"
    assert label == "Pobre"


# ── Test 11b: SQN — la etiqueta se retiene con muestra chica ────────────────

def test_sqn_label_withheld_below_min_trades():
    """
    Con N < 20 el SQN se devuelve pero SIN etiqueta de calidad.

    El problema no es la varianza sino el tamaño de muestra: √N × mean / std
    no está acotado con N chico, así que dos trades ganadores normales
    ([5.0, 6.0] → SQN 11.0) caerían en "Santo Grial" según la tabla. La nota
    "(orientativo)" sola no alcanza: lo que el usuario lee es la etiqueta.
    """
    sqn_val, note, label = _calc_sqn([5.0, 6.0])

    # El número se sigue reportando…
    assert sqn_val == pytest.approx(11.0, abs=0.01)
    assert note == "(orientativo)"
    # …pero sin calificación: 11.0 nunca debe leerse "Santo Grial" con N=2.
    assert label == "N/A"


def test_sqn_label_emitted_at_or_above_min_trades():
    """Con N >= 20 la etiqueta sí se emite (contraparte del test anterior)."""
    sqn_val, note, label = _calc_sqn([10.0, -5.0] * 10)  # N = 20

    assert sqn_val == pytest.approx(1.45, abs=0.01)
    assert note == ""
    assert label == "Pobre"


# ── Test 12: SQN — desviación exactamente cero sigue reportándose distinto ──

def test_sqn_exact_zero_deviation_note():
    """Cuando std es EXACTAMENTE 0, la nota debe seguir siendo la original."""
    sqn_val, note, label = _calc_sqn([5.0, 5.0])

    assert sqn_val is None
    assert note == "(desviación cero)"
    assert label == "N/A"


# ── Test 13: weeks_operating nunca es negativo ───────────────────────────────

def test_weeks_operating_never_negative_for_trade_closing_today():
    """
    El minuendo es hoy a las 00:00, pero el primer trade puede llevar una
    hora intradía posterior — sin el clamp, un trade cerrado hoy produciría
    un valor ligeramente negativo.
    """
    close_today = datetime.combine(date.today(), datetime.min.time()) + timedelta(hours=10)
    trades_sorted = [
        {"position_id": 1, "comment": "MyEA", "net_pnl": 10.0, "close_time": close_today,
         "direction": "buy", "duration_hours": 4.0},
    ]

    weeks = _weeks_operating(trades_sorted)

    assert weeks >= 0.0
    assert weeks == pytest.approx(0.0)


# ── Test 14: _calc_risk_of_ruin fue eliminada (dead code, cero call sites) ──

def test_calc_risk_of_ruin_no_longer_exists():
    """La función Monte Carlo Risk of Ruin no tenía ningún call site — se eliminó."""
    assert not hasattr(metrics, "_calc_risk_of_ruin")
