"""
conftest.py - Fixtures compartidos del arnés de verdad ejecutable (P-A).

Este paquete NO corrige nada. Sólo observa y fija el comportamiento actual.

Tres capas:
  - test_char_*.py  → caracterización: fija lo que HAY (incluyendo defectos).
  - test_prop_*.py  → propiedades (Hypothesis): invariantes para TODA entrada.
  - test_diff_*.py  → diferencial: métricas contra un oráculo independiente.

Todos los datos son inventados y hardcodeados. Ningún .xlsx real.

Determinismo: metrics.py e incubation_validator.py llaman a date.today() /
datetime.now() en 6 sitios. Ambos módulos hacen `from datetime import date,
datetime`, por lo que el monkeypatch va sobre el NOMBRE IMPORTADO EN EL MÓDULO
(metrics.date, incubation_validator.date), nunca sobre datetime.date.
"""

import pytest
from datetime import date, datetime, timedelta

import metrics
import incubation_validator


# ── Fecha congelada del arnés ──────────────────────────────────────────────
# Elegida arbitrariamente pero fija: todo test que dependa del reloj la usa.
FROZEN_TODAY = date(2026, 7, 16)
FROZEN_NOW = datetime(2026, 7, 16, 12, 0, 0)


class _FrozenDate(date):
    """date con today() congelado. Subclase real: preserva el resto de la API."""

    @classmethod
    def today(cls):
        return FROZEN_TODAY


class _FrozenDateTime(datetime):
    """datetime con now() congelado. Subclase real: preserva combine/min/max/fromisoformat."""

    @classmethod
    def now(cls, tz=None):
        return FROZEN_NOW

    @classmethod
    def today(cls):
        return FROZEN_NOW


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch):
    """
    Congela el reloj en TODOS los tests de este paquete.

    autouse: el determinismo no es opt-in. Un test del arnés que dependa del
    reloj real es un test que miente mañana.
    """
    monkeypatch.setattr(metrics, "date", _FrozenDate)
    monkeypatch.setattr(metrics, "datetime", _FrozenDateTime)
    monkeypatch.setattr(incubation_validator, "date", _FrozenDate)
    monkeypatch.setattr(incubation_validator, "datetime", _FrozenDateTime)
    return FROZEN_TODAY


# ── Constructores de datos ─────────────────────────────────────────────────

def make_trade(net_pnl, close_time=None, direction="buy", symbol="EURUSD",
               duration_hours=1.0, comment="MyEA", position_id=1):
    """
    Trade mínimo con TODAS las claves que metrics.py accede por t[...] (no .get).

    Claves obligatorias (KeyError si faltan): net_pnl, close_time, direction,
    symbol. duration_hours y comment son opcionales (.get).
    """
    return {
        "position_id": position_id,
        "symbol": symbol,
        "direction": direction,
        "close_time": close_time if close_time is not None else datetime(2026, 1, 2, 12, 0, 0),
        "net_pnl": float(net_pnl),
        "duration_hours": duration_hours,
        "comment": comment,
    }


def make_trades(pnls, start=datetime(2026, 1, 2, 12, 0, 0), comment="MyEA"):
    """Serie de trades, uno por día, con los P&L dados en orden cronológico."""
    return [
        make_trade(p, close_time=start + timedelta(days=i), comment=comment, position_id=1000 + i)
        for i, p in enumerate(pnls)
    ]


def make_config(ea_name="MyEA", capital=10000.0, active=True, magic="9001"):
    """Config con el único valor que llega a la matemática: capital."""
    return {
        "mappings": {
            ea_name: {
                "magic": magic,
                "alias": f"{ea_name} Alias",
                "instrument": "EURUSD",
                "capital": capital,
                "active": active,
            }
        }
    }


# ── Referencias del validador (bt / mc / spp / live) ───────────────────────

def make_bt(**over):
    """Backtest de referencia del validador, sano y completo."""
    bt = {
        "win_rate": 55.0,
        "profit_factor": 1.5,
        "payout_ratio": 1.2,
        "expectancy": 10.0,
        "avg_bars": 20.0,
        "max_dd_pct": 10.0,
        "max_consec_losses": 5,
        "trades_total": 120,
        "months": 12.0,
        "worst_dd_1m": 8.0,
        "worst_dd_3m": 12.0,
        "stagnation_days": 30.0,
    }
    bt.update(over)
    return bt


def make_live(**over):
    """Métricas live del validador, sanas y completas."""
    live = {
        "total_trades": 100,
        "weeks_operating": 40.0,
        "win_rate": 55.0,
        "profit_factor": 1.5,
        "payout_ratio": 1.2,
        "expectancy": 10.0,
        "max_dd_pct": 5.0,
        "avg_bars_live": 20.0,
        "max_consec_losses": 5,
        "stagnation_days": 10.0,
    }
    live.update(over)
    return live


def make_mc(max_dd=12.0):
    return {"max_dd": max_dd}


def make_spp(expectancy_median=10.0):
    return {"expectancy_median": expectancy_median}


@pytest.fixture
def healthy_validator_args():
    """(bt, mc_retest, mc_trades, spp, live) que produce un score completo."""
    return make_bt(), make_mc(12.0), make_mc(14.0), make_spp(10.0), make_live()


# ── Referencias de incubación ──────────────────────────────────────────────

_MC_LEVEL_KEYS = (
    "win_rate", "profit_factor", "expectancy", "avg_trade", "payout_ratio",
    "ret_dd_ratio", "max_dd_pct", "max_consec_losses", "stagnation_days",
)


def make_mc_level(**over):
    """Un nivel de confianza MC completo (todas las claves que se leen)."""
    lvl = {
        "win_rate": 45.0,
        "profit_factor": 1.1,
        "expectancy": 5.0,
        "avg_trade": 5.0,
        "payout_ratio": 1.0,
        "ret_dd_ratio": 1.5,
        "max_dd_pct": 20.0,
        "max_consec_losses": 8,
        "stagnation_days": 60.0,
    }
    lvl.update(over)
    return lvl


def make_mc_block(c95_over=None, c50_over=None):
    """Bloque MC con ambos niveles de confianza."""
    return {
        "confidence_95": make_mc_level(**(c95_over or {})),
        "confidence_50": make_mc_level(
            **{"win_rate": 50.0, "profit_factor": 1.3, "expectancy": 8.0,
               "avg_trade": 8.0, "payout_ratio": 1.1, "ret_dd_ratio": 2.0,
               "max_dd_pct": 15.0, "max_consec_losses": 6, "stagnation_days": 45.0,
               **(c50_over or {})}
        ),
    }


def make_reference(**over):
    """
    reference_data de incubación, completo y sano.

    Ojo: incubation_validator.py NO lee ningún archivo de config. Todos sus
    umbrales son literales hardcodeados. Sólo hay que inyectar esto.
    """
    ref = {
        "date_added": "2026-01-02",
        "backtest": {
            "win_rate": 55.0,
            "total_trades": 120,
            "bt_period": "2025.01.01 - 2025.12.31",
            "profit_factor": 1.5,
            "expectancy": 10.0,
            "payout_ratio": 1.2,
            "ret_dd_ratio": 2.5,
            "max_dd_pct": 10.0,
            "max_consec_losses": 5,
            "stagnation_days": 30.0,
            "avg_trade": 10.0,
        },
        "mc_manipulation": make_mc_block(),
        "mc_retest": make_mc_block(),
        "spp": {
            "median_max_dd_pct": 12.0,
            "median_payout_ratio": 1.15,
            "median_avg_trade": 9.0,
            "median_ret_dd_ratio": 2.2,
            "median_stagnation_days": 35.0,
            "median_win_rate": 53.0,
            "median_profit_factor": 1.4,
        },
    }
    ref.update(over)
    return ref


def make_live_metrics(**over):
    """
    live_metrics de incubación = salida de calculate_ea_metrics.

    Sólo se incluyen las claves que incubation_validator realmente lee.
    """
    lm = {
        "total_trades": 50,
        "winning_trades": 28,
        "win_rate": 56.0,
        "profit_factor": 1.6,
        "expectancy": 11.0,
        "payout_ratio": 1.25,
        "ret_dd": 2.6,
        "max_dd_pct": 9.0,
        "max_consec_losses": 4,
        "stagnation_days": 25.0,
        "avg_trade": 11.0,
        "trades": [],
    }
    lm.update(over)
    return lm
