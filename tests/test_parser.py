"""
test_parser.py - Tests unitarios para parser.py.

No se usa ningún archivo .xlsx real. Se testean las funciones puras
exportadas directamente desde parser.py.
"""

import pytest
from datetime import datetime
from parser import (
    SYSTEM_COMMENT_PREFIX,
    _to_float,
    _parse_date,
    merge_trades,
)


# ── Test 1: _parse_orders filtra comentarios de sistema ──────────────────────

def test_system_comment_prefix_constant():
    """
    SYSTEM_COMMENT_PREFIX es '['. Comentarios MT5 como '[sl 1.23]'
    deben ser identificados por este prefijo y excluidos del order_map.
    """
    assert SYSTEM_COMMENT_PREFIX == "["
    assert "[sl 1.09250]".startswith(SYSTEM_COMMENT_PREFIX)
    assert "[tp 2700.0]".startswith(SYSTEM_COMMENT_PREFIX)
    assert "MyEA".startswith(SYSTEM_COMMENT_PREFIX) is False
    assert "".startswith(SYSTEM_COMMENT_PREFIX) is False


# ── Test 2: _to_float maneja strings, None y comas ───────────────────────────

def test_to_float_conversions():
    """
    _to_float reemplaza coma por punto (formato europeo: "1,5" → 1.5).
    No soporta separador de miles con coma ("1,234.5" → inválido → 0.0).
    Este comportamiento es correcto para MT5 que usa punto decimal.
    """
    assert _to_float(100.0) == 100.0
    assert _to_float("99.5") == 99.5
    assert _to_float("1,5") == 1.5       # formato europeo: coma decimal → punto
    assert _to_float("1,234.5") == 0.0   # separador de miles no soportado → 0.0
    assert _to_float(None) == 0.0        # None → default 0.0
    assert _to_float(None, default=-1.0) == -1.0
    assert _to_float("invalid") == 0.0


# ── Test 3: _parse_date maneja múltiples formatos ────────────────────────────

def test_parse_date_formats():
    """_parse_date acepta datetime directo o string en formato MT5."""
    dt = datetime(2026, 1, 15, 10, 30, 0)

    # Datetime ya parseado → devuelve igual
    assert _parse_date(dt) == dt

    # Formato MT5 estándar
    assert _parse_date("2026.01.15 10:30:00") == dt

    # Formato ISO
    assert _parse_date("2026-01-15 10:30:00") == dt

    # None → None
    assert _parse_date(None) is None


# ── Tests 4-8: merge_trades() — función central del append mode ──────────────

def _make_trade(position_id, close_day, comment="MyEA", net_pnl=100.0):
    """Helper: trade mínimo para tests de merge."""
    return {
        "position_id": position_id,
        "comment": comment,
        "net_pnl": net_pnl,
        "close_time": datetime(2026, 1, close_day, 12, 0, 0),
        "direction": "buy",
    }


def test_merge_trades_deduplication():
    """
    Trades con el mismo position_id no se duplican.
    El trade existente tiene precedencia sobre el nuevo.
    """
    existing = [_make_trade(1001, 2, net_pnl=99.0)]
    new_trades = [
        _make_trade(1001, 2, net_pnl=999.0),  # mismo ID, distinto pnl → debe ignorarse
        _make_trade(1002, 5, net_pnl=50.0),   # nuevo ID → debe agregarse
    ]

    result = merge_trades(existing, new_trades)

    assert len(result) == 2
    # El trade existente (net_pnl=99) tiene precedencia
    trade_1001 = next(t for t in result if t["position_id"] == 1001)
    assert trade_1001["net_pnl"] == pytest.approx(99.0)


def test_merge_trades_sort_order():
    """
    El resultado siempre está ordenado por close_time ascendente.
    """
    existing = [_make_trade(1003, 10)]  # Jan 10
    new_trades = [
        _make_trade(1001, 2),   # Jan 2 — más antiguo
        _make_trade(1002, 5),   # Jan 5
    ]

    result = merge_trades(existing, new_trades)

    dates = [t["close_time"] for t in result]
    assert dates == sorted(dates)
    assert result[0]["position_id"] == 1001  # Jan 2 primero


def test_merge_trades_empty_existing():
    """merge_trades con existing vacío devuelve todos los new_trades."""
    new_trades = [_make_trade(1001, 2), _make_trade(1002, 5)]
    result = merge_trades([], new_trades)

    assert len(result) == 2
    assert {t["position_id"] for t in result} == {1001, 1002}


def test_merge_trades_empty_new():
    """merge_trades con new_trades vacío devuelve existing sin cambios."""
    existing = [_make_trade(1001, 2), _make_trade(1002, 5)]
    result = merge_trades(existing, [])

    assert len(result) == 2
    assert {t["position_id"] for t in result} == {1001, 1002}


def test_merge_trades_none_close_time_sorts_to_end():
    """
    Trades con close_time=None van al FINAL de la lista, no al inicio.
    Un None al inicio corrupiría la equity curve (que asume orden cronológico).
    """
    trade_with_none = {
        "position_id": 9999,
        "comment": "MyEA",
        "net_pnl": 0.0,
        "close_time": None,
        "direction": "buy",
    }
    existing = [_make_trade(1001, 2), _make_trade(1002, 5)]
    new_trades = [trade_with_none]

    result = merge_trades(existing, new_trades)

    # El trade sin close_time debe ir al final
    assert result[-1]["position_id"] == 9999
    assert result[0]["position_id"] == 1001
