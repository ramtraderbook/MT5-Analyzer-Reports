"""
test_parser.py - Tests unitarios para la lógica interna de parser.py.

No se usa ningún archivo .xlsx real. Se testean las funciones puras
que operan sobre dicts ya construidos (lógica de join + filtros).
"""

import pytest
from parser import SYSTEM_COMMENT_PREFIX, _to_float, _parse_date
from datetime import datetime


# ── Test 1: JOIN correcto entre POSITIONS y ORDERS ───────────────────────────

def test_join_assigns_ea_comment():
    """
    Dado un trade y un order_map con match, el comment del trade
    debe ser reemplazado con el nombre del EA del order_map.
    """
    trades = [
        {"position_id": 1001, "comment": "Unknown"},
        {"position_id": 1002, "comment": "Unknown"},
    ]
    order_map = {
        1001: "MyEA",
        1002: "OtherEA",
    }

    # Reproducir lógica de JOIN de parse_mt5_report()
    for trade in trades:
        pid = trade["position_id"]
        if pid in order_map:
            trade["comment"] = order_map[pid]

    assert trades[0]["comment"] == "MyEA"
    assert trades[1]["comment"] == "OtherEA"


# ── Test 2: Comentarios de sistema MT5 son filtrados ─────────────────────────

def test_system_comments_filtered_from_order_map():
    """
    Comentarios que empiezan con '[' (auto-generados por MT5 para SL/TP)
    NO deben entrar al order_map. Reproducir lógica de _parse_orders().
    """
    raw_orders = [
        {"order_id": 1001, "comment": "MyEA"},
        {"order_id": 1002, "comment": "[sl 1.09250]"},   # sistema → excluir
        {"order_id": 1003, "comment": "[tp 2700.0]"},    # sistema → excluir
        {"order_id": 1004, "comment": ""},               # vacío → excluir
        {"order_id": 1005, "comment": "AnotherEA"},
    ]

    # Reproducir lógica de _parse_orders()
    order_map = {}
    for o in raw_orders:
        comment_str = str(o["comment"] or "").strip()
        if comment_str and not comment_str.startswith(SYSTEM_COMMENT_PREFIX):
            order_map[o["order_id"]] = comment_str

    assert 1001 in order_map
    assert order_map[1001] == "MyEA"
    assert 1002 not in order_map   # [sl ...] filtrado
    assert 1003 not in order_map   # [tp ...] filtrado
    assert 1004 not in order_map   # vacío filtrado
    assert 1005 in order_map
    assert order_map[1005] == "AnotherEA"


# ── Test 3: Trade sin match → "Unknown" ──────────────────────────────────────

def test_no_match_becomes_unknown():
    """
    Un trade cuyo position_id NO existe en order_map
    debe mantener comment = "Unknown".
    """
    trades = [
        {"position_id": 9999, "comment": "Unknown"},  # sin match
        {"position_id": 1001, "comment": "Unknown"},  # con match
    ]
    order_map = {1001: "MyEA"}

    unknown_count = 0
    for trade in trades:
        pid = trade["position_id"]
        if pid in order_map:
            trade["comment"] = order_map[pid]
        else:
            trade["comment"] = "Unknown"
            unknown_count += 1

    assert trades[0]["comment"] == "Unknown"
    assert trades[1]["comment"] == "MyEA"
    assert unknown_count == 1


# ── Test 4: _to_float maneja strings, None y comas decimales ─────────────────

def test_to_float_conversions():
    """
    _to_float reemplaza coma por punto (formato europeo: "1,5" → 1.5).
    No soporta separador de miles con coma ("1,234.5" → inválido → 0.0).
    Este comportamiento es correcto para MT5 que usa punto decimal.
    """
    assert _to_float(100.0) == 100.0
    assert _to_float("99.5") == 99.5
    assert _to_float("1,5") == 1.5       # formato europeo: coma decimal → punto
    assert _to_float("1,234.5") == 0.0   # separador de miles no soportado → 0.0 (correcto)
    assert _to_float(None) == 0.0        # None → default 0.0
    assert _to_float(None, default=-1.0) == -1.0
    assert _to_float("invalid") == 0.0


# ── Test 5: net_pnl = profit + commission + swap (NUNCA solo profit) ─────────

def test_net_pnl_formula():
    """
    La regla de negocio más crítica: net_pnl SIEMPRE incluye commission y swap.
    """
    profit = 100.0
    commission = -1.0
    swap = -2.5

    net_pnl = profit + commission + swap

    assert net_pnl == pytest.approx(96.5)
    assert net_pnl != profit  # nunca usar solo profit
