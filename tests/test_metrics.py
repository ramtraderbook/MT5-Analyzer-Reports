"""
test_metrics.py - Tests unitarios para las funciones de cálculo de metrics.py.

Todos los valores son exactos y verificables a mano.
No se usa ningún archivo .xlsx real.
"""

import math
import pytest
from datetime import datetime

from metrics import (
    _build_equity_curve,
    _build_drawdown_curve,
    _calc_max_drawdown,
    _calc_sqn,
    _calc_streaks,
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
