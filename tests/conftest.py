"""
conftest.py - Fixtures compartidos para todos los tests de EA Analyzer.

Todos los datos son 100% inventados (hardcodeados). No se usa ningún archivo .xlsx real.
"""

import pytest
from datetime import datetime


# ── Trades hardcodeados con valores exactos y calculables ───────────────────

TRADE_A = {
    "position_id": 1001,
    "symbol": "EURUSD",
    "direction": "buy",
    "volume": 0.1,
    "open_time": datetime(2026, 1, 2, 10, 0, 0),
    "close_time": datetime(2026, 1, 2, 14, 0, 0),
    "open_price": 1.08500,
    "close_price": 1.08750,
    "sl": 1.08200,
    "tp": 1.09000,
    "commission": -1.0,
    "swap": 0.0,
    "profit": 100.0,
    "net_pnl": 99.0,          # profit + commission + swap = 100 - 1 + 0
    "duration_hours": 4.0,
    "comment": "MyEA",
}

TRADE_B = {
    "position_id": 1002,
    "symbol": "EURUSD",
    "direction": "sell",
    "volume": 0.1,
    "open_time": datetime(2026, 1, 5, 9, 0, 0),
    "close_time": datetime(2026, 1, 5, 11, 0, 0),
    "open_price": 1.09000,
    "close_price": 1.09250,
    "sl": None,
    "tp": None,
    "commission": -1.0,
    "swap": -2.0,
    "profit": -50.0,
    "net_pnl": -53.0,         # -50 - 1 - 2
    "duration_hours": 2.0,
    "comment": "MyEA",
}

TRADE_C = {
    "position_id": 1003,
    "symbol": "XAUUSD",
    "direction": "buy",
    "volume": 0.05,
    "open_time": datetime(2026, 1, 10, 8, 0, 0),
    "close_time": datetime(2026, 1, 10, 16, 0, 0),
    "open_price": 2650.0,
    "close_price": 2680.0,
    "sl": 2620.0,
    "tp": 2700.0,
    "commission": -2.0,
    "swap": 1.0,
    "profit": 200.0,
    "net_pnl": 199.0,         # 200 - 2 + 1
    "duration_hours": 8.0,
    "comment": "MyEA",
}

# Trade sin match en ORDERS → debe quedar como "Unknown"
TRADE_UNKNOWN = {
    "position_id": 9999,
    "symbol": "GBPUSD",
    "direction": "buy",
    "volume": 0.1,
    "open_time": datetime(2026, 1, 15, 10, 0, 0),
    "close_time": datetime(2026, 1, 15, 12, 0, 0),
    "open_price": 1.27000,
    "close_price": 1.27200,
    "sl": None,
    "tp": None,
    "commission": -0.5,
    "swap": 0.0,
    "profit": 20.0,
    "net_pnl": 19.5,
    "duration_hours": 2.0,
    "comment": "Unknown",
}


@pytest.fixture
def sample_trades():
    """Lista de 3 trades ganadores/perdedores con valores exactos."""
    return [TRADE_A.copy(), TRADE_B.copy(), TRADE_C.copy()]


@pytest.fixture
def sample_config():
    """Config mínimo con capital conocido para MyEA."""
    return {
        "mappings": {
            "MyEA": {
                "magic": "9001",
                "alias": "MyEA Test",
                "instrument": "EURUSD",
                "capital": 10000.0,
                "active": True,
            }
        }
    }


@pytest.fixture
def order_map_valid():
    """order_map simulado: position_id → comment (EA name válido)."""
    return {
        1001: "MyEA",
        1002: "MyEA",
        1003: "OtherEA",
    }


@pytest.fixture
def order_map_with_system_comments():
    """order_map con comentarios de sistema MT5 que deben ser filtrados."""
    return {
        1001: "MyEA",
        1002: "[sl 1.09250]",  # comentario de sistema → debe excluirse
        1003: "[tp 2700.0]",   # comentario de sistema → debe excluirse
    }
