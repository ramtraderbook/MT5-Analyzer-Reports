"""
test_prop_metrics.py - Propiedades (Hypothesis) para metrics.calculate_ea_metrics.

Capa P-B del arnés. Igual que en los otros dos archivos: un contraejemplo real
de Hypothesis se documenta con @pytest.mark.xfail(strict=True) y el repro
mínimo, nunca se debilita la propiedad para forzarla a pasar.

Anclas contra el árbol de trabajo real (metrics.py) y, cuando aplica,
contra docs/metrics-formulas.md y docs/known-issues.md -- nunca contra
"ground-truth.md" o "scratchpad/", que no existen en este repo.
"""

import copy
import math
from datetime import datetime, timedelta

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import metrics
from conftest import make_config, make_trades


def _raw_trade(net_pnl, close_time=None, direction="buy", symbol="EURUSD", duration_hours=1.0, position_id=1):
    """Trade crudo SIN pasar por conftest.make_trade -- make_trade fuerza
    `float(net_pnl)`, lo que impediría probar net_pnl con tipos no numéricos."""
    return {
        "position_id": position_id,
        "symbol": symbol,
        "direction": direction,
        "close_time": close_time if close_time is not None else datetime(2026, 1, 2, 12, 0, 0),
        "net_pnl": net_pnl,
        "duration_hours": duration_hours,
        "comment": "MyEA",
    }


REQUIRED_TRADE_KEYS = ("net_pnl", "close_time", "direction", "symbol")


@st.composite
def well_formed_trades(draw):
    """Lista de trades bien formados (las 4 claves requeridas siempre
    presentes, tipos válidos) con P&L, dirección, símbolo y fechas variados --
    incluyendo valores extremos (P&L enorme/negativo, direcciones/símbolos
    fuera del set usual "buy"/"sell", trades sin duration_hours)."""
    n = draw(st.integers(min_value=0, max_value=15))
    trades = []
    for i in range(n):
        pnl = draw(st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False))
        direction = draw(st.sampled_from(["buy", "sell", "BUY", "unknown", ""]))
        symbol = draw(st.sampled_from(["EURUSD", "XAUUSD", "GBPJPY", ""]))
        duration = draw(st.one_of(
            st.none(), st.floats(min_value=0.0, max_value=2000.0, allow_nan=False, allow_infinity=False)
        ))
        day_offset = draw(st.integers(min_value=0, max_value=3650))
        trades.append(_raw_trade(
            pnl, close_time=datetime(2020, 1, 1) + timedelta(days=day_offset),
            direction=direction, symbol=symbol, duration_hours=duration, position_id=i,
        ))
    return trades


clean_floats = st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False)
positive_floats = st.floats(min_value=0.01, max_value=1e7, allow_nan=False, allow_infinity=False)


# ── 1. RANGO ──────────────────────────────────────────────────────────────────

@given(trades=well_formed_trades(), capital=positive_floats)
@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_metrics_ranges(trades, capital, frozen_clock):
    """Con trades bien formados y capital > 0: win_rate in [0,100];
    max_dd_pct/max_dd_dollar >= 0; weeks_operating >= 0; stagnation_days >= 0
    (entero); sqn/sharpe_ratio/ret_dd/recovery_factor None o float finito;
    profit_factor/payout_ratio son "∞" (str) o un float finito >= 0 (unión de
    tipos verificada contra metrics.py:637-640 y documentada como inocua en
    docs/known-issues.md §7)."""
    result = metrics.calculate_ea_metrics("MyEA", trades, make_config(capital=capital))

    assert result["total_trades"] == len(trades)
    assert 0.0 <= result["win_rate"] <= 100.0
    assert result["max_dd_pct"] >= 0.0
    assert result["max_dd_dollar"] >= 0.0
    assert result["weeks_operating"] >= 0.0
    assert isinstance(result["stagnation_days"], int) and result["stagnation_days"] >= 0

    for key in ("sqn", "sharpe_ratio", "ret_dd", "recovery_factor"):
        v = result[key]
        assert v is None or (isinstance(v, (int, float)) and not math.isnan(v) and not math.isinf(v))

    for key in ("profit_factor", "payout_ratio"):
        v = result[key]
        assert v == "∞" or (isinstance(v, (int, float)) and not math.isnan(v) and v >= 0.0)

    assert result["winning_trades"] + result["losing_trades"] == result["total_trades"]
    assert 0 <= result["winning_trades"] <= result["total_trades"]


# ── 2. DETERMINISMO ──────────────────────────────────────────────────────────

@given(trades=well_formed_trades(), capital=positive_floats)
@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_calculate_ea_metrics_is_deterministic(trades, capital, frozen_clock):
    """Misma entrada -> misma salida (dict deep-equal), llamando dos veces con
    copias independientes de la lista de trades."""
    config = make_config(capital=capital)
    trades_copy = copy.deepcopy(trades)

    r1 = metrics.calculate_ea_metrics("MyEA", trades, config)
    r2 = metrics.calculate_ea_metrics("MyEA", trades_copy, config)
    assert r1 == r2


# ── 3. SIN EXCEPCIONES NO MANEJADAS: entrada bien tipada pero extrema ────────

@given(
    trades=well_formed_trades(),
    capital=st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
)
@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_no_crash_on_well_formed_trades_with_extreme_config(trades, capital, frozen_clock):
    """Trades bien formados (las 4 claves requeridas presentes, tipos
    correctos) con capital extremo (incluido negativo o cero -- docs/known-issues.md
    §7: 'capital <= 0 hace peak_abs <= 0 y el DD% cae silenciosamente a 0.0')
    nunca deben crashear."""
    result = metrics.calculate_ea_metrics("MyEA", trades, make_config(capital=capital))
    assert isinstance(result, dict)
    assert result["total_trades"] == len(trades)


@given(trades=st.just([]))
@settings(max_examples=1, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_no_crash_on_empty_trades(trades, frozen_clock):
    """Lista vacía -> _empty_metrics (:875), no un crash por índice/división."""
    result = metrics.calculate_ea_metrics("MyEA", trades, make_config())
    assert result["total_trades"] == 0
    assert result["win_rate"] == 0.0


# ── 4. SIN EXCEPCIONES NO MANEJADAS: claves requeridas faltantes ────────────

@st.composite
def trade_missing_one_required_key(draw):
    missing_key = draw(st.sampled_from(REQUIRED_TRADE_KEYS))
    pnl = draw(st.floats(min_value=-1000.0, max_value=1000.0, allow_nan=False, allow_infinity=False))
    trade = _raw_trade(pnl)
    del trade[missing_key]
    return trade, missing_key


@given(data=trade_missing_one_required_key())
@settings(max_examples=50, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@pytest.mark.xfail(strict=True, reason=(
    "COUNTEREXAMPLE: un trade sin 'direction', 'symbol', 'net_pnl' o 'close_time' "
    "-> KeyError no capturado dentro de calculate_ea_metrics (t[...] en vez de "
    "t.get(...): metrics.py:550 net_pnl, :556 close_time, :590-591 direction, "
    ":629 symbol). Repro mínimo: calculate_ea_metrics("
    "'EA', [{'net_pnl': 1.0, 'close_time': datetime(2026,1,1), 'symbol': "
    "'EURUSD'}], {}) (falta 'direction') -> KeyError('direction')."
))
def test_missing_required_trade_key_crashes(data, frozen_clock):
    trade, _missing_key = data
    metrics.calculate_ea_metrics("EA", [trade], make_config())


# ── 5. SIN EXCEPCIONES NO MANEJADAS: close_time malformado ───────────────────

def _is_valid_iso(s):
    try:
        datetime.fromisoformat(s)
        return True
    except ValueError:
        return False


bad_iso_text = st.text(min_size=1, max_size=20).filter(lambda s: not _is_valid_iso(s))


@given(bad=bad_iso_text)
@settings(max_examples=50, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@pytest.mark.xfail(strict=True, reason=(
    "COUNTEREXAMPLE: close_time='<cadena no ISO>' -> ValueError no capturado vía "
    "datetime.fromisoformat (metrics.py:545 en sort_key, también :111/:132/:517/:700). "
    "Repro mínimo: calculate_ea_metrics('EA', "
    "[{'net_pnl': 1.0, 'close_time': 'not-a-date', 'direction': 'buy', "
    "'symbol': 'EURUSD'}], {}) -> ValueError: Invalid isoformat string."
))
def test_malformed_close_time_string_crashes(bad, frozen_clock):
    trade = _raw_trade(1.0, close_time=bad)
    metrics.calculate_ea_metrics("EA", [trade], make_config())


# ── 6. SIN EXCEPCIONES NO MANEJADAS: net_pnl no numérico ─────────────────────

@given(bad=st.one_of(st.text(min_size=1, max_size=10), st.none()))
@settings(max_examples=50, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@pytest.mark.xfail(strict=True, reason=(
    "COUNTEREXAMPLE: net_pnl=<string o None> -> TypeError no capturado. "
    "metrics.py:559-560 compara `p > 0` / `p <= 0` directamente sobre "
    "net_pnl_list sin convertir a float -- comparar str/None con int lanza "
    "TypeError. Repro mínimo: calculate_ea_metrics('EA', [{'net_pnl': 'abc', "
    "'close_time': datetime(2026,1,1), 'direction': 'buy', 'symbol': 'EURUSD'}], "
    "{}) -> TypeError: '>' not supported between instances of 'str' and 'int'."
))
def test_non_numeric_net_pnl_crashes(bad, frozen_clock):
    trade = _raw_trade(bad)
    metrics.calculate_ea_metrics("EA", [trade], make_config())


# ── 7. CONSISTENCIA win/loss vs total_trades bajo net_pnl=NaN ────────────────

@st.composite
def trades_with_one_nan_pnl(draw):
    n = draw(st.integers(min_value=1, max_value=5))
    idx = draw(st.integers(min_value=0, max_value=n - 1))
    trades = make_trades([100.0] * n)
    trades[idx]["net_pnl"] = float("nan")
    return trades


@given(trades=trades_with_one_nan_pnl())
@settings(max_examples=30, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@pytest.mark.xfail(strict=True, reason=(
    "COUNTEREXAMPLE: un trade con net_pnl=float('nan') NO crashea, pero "
    "desaparece silenciosamente de AMBAS particiones win/loss (metrics.py:559-560 "
    "`p > 0` / `p <= 0` son ambas False para NaN), así que "
    "winning_trades + losing_trades < total_trades, y su P&L se esfuma de "
    "net_profit sin ningún error ni señal de SIN DATOS. Repro mínimo: "
    "calculate_ea_metrics('EA', [{'net_pnl': float('nan'), ...}, "
    "{'net_pnl': 100.0, ...}], {}) -> total_trades=2, winning_trades=1, "
    "losing_trades=0 (1+0 != 2)."
))
def test_winning_plus_losing_equals_total_trades_under_nan_pnl(trades, frozen_clock):
    result = metrics.calculate_ea_metrics("MyEA", trades, make_config())
    assert result["winning_trades"] + result["losing_trades"] == result["total_trades"]


# ── 8. BOOTSTRAP — metrics.calculate_bootstrap_risk ──────────────────────────
#
# Standalone, no conectada a calculate_ea_metrics/validator.py -- ver
# docs/known-issues.md y la nota de scope en metrics.calculate_bootstrap_risk.
# max_examples se mantiene en 50 (no 200, la convención del resto del
# archivo): cada ejemplo corre un bootstrap vectorizado de
# BOOTSTRAP_ITERATIONS=10000 rutas, así que 200 ejemplos multiplicarían el
# costo sin ganar cobertura de propiedad adicional -- el espacio de entrada
# (listas finitas de floats, capital positivo) es simple comparado con
# well_formed_trades().

finite_pnls = st.lists(
    st.floats(min_value=-1e5, max_value=1e5, allow_nan=False, allow_infinity=False),
    min_size=metrics.MIN_TRADES_FOR_BOOTSTRAP,
    max_size=40,
)


@given(pnls=finite_pnls, capital=positive_floats)
@settings(max_examples=50, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_bootstrap_risk_ranges_and_ordering(pnls, capital):
    """Con N >= MIN_TRADES_FOR_BOOTSTRAP y capital > 0: percentiles ordenados
    p50 <= p95 <= p99; cada probabilidad de ruina en [0,1]; la probabilidad de
    ruina es monótonamente NO CRECIENTE a medida que sube el umbral (romper el
    50% implica haber roto el 10%); max_dd_pct >= 0 en las tres bandas."""
    result = metrics.calculate_bootstrap_risk(pnls, capital)

    assert result is not None
    assert result["max_dd_pct_p50"] >= 0.0
    assert result["max_dd_pct_p50"] <= result["max_dd_pct_p95"] <= result["max_dd_pct_p99"]

    probs = result["ruin_probability"]
    ordered_thresholds = sorted(probs.keys())
    for threshold in ordered_thresholds:
        p = probs[threshold]
        assert 0.0 <= p <= 1.0

    for lo, hi in zip(ordered_thresholds, ordered_thresholds[1:]):
        assert probs[hi] <= probs[lo]


@given(pnls=finite_pnls, capital=positive_floats)
@settings(max_examples=50, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_bootstrap_risk_deterministic_under_hypothesis_inputs(pnls, capital):
    """Mismo seed (el default, BOOTSTRAP_SEED) -> misma salida byte-idéntica,
    generalizado sobre la misma familia de entradas que el test de rango de
    arriba, no sólo sobre un fixture fijo (ver test_bootstrap_risk_same_seed_is_byte_identical
    en tests/test_metrics.py para el caso puntual)."""
    r1 = metrics.calculate_bootstrap_risk(pnls, capital)
    r2 = metrics.calculate_bootstrap_risk(list(pnls), capital)
    assert r1 == r2


# ── 9. PSR — metrics.calculate_psr ───────────────────────────────────────────
#
# Standalone, sin conectar a validator.py -- misma disciplina que el bootstrap.
# Sin RNG y O(n), así que max_examples=200 (la convención del archivo) es barato.

psr_pnls = st.lists(
    st.floats(min_value=-1e5, max_value=1e5, allow_nan=False, allow_infinity=False),
    min_size=metrics.MIN_TRADES_FOR_PSR,
    max_size=60,
)


@given(pnls=psr_pnls)
@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_psr_is_a_probability_or_structured_unavailable(pnls):
    """Para toda entrada finita con N >= MIN_TRADES_FOR_PSR: o bien PSR es una
    probabilidad en [0,1] con momentos finitos, o bien devuelve la forma
    estructurada {"available": False, "reason": ...}. Nunca una excepcion,
    nunca un numero fuera de [0,1] (el modo de falla del annualize de quantstats)."""
    result = metrics.calculate_psr(pnls)
    assert isinstance(result, dict)
    assert result["available"] in (True, False)
    if result["available"]:
        assert 0.0 <= result["psr"] <= 1.0
        assert math.isfinite(result["skew"])
        assert math.isfinite(result["kurtosis"])
        # kurtosis (no-excess) >= 1 por la desigualdad de medias de potencias
        assert result["kurtosis"] >= 1.0 - 1e-9
    else:
        assert isinstance(result["reason"], str) and result["reason"]


@given(pnls=psr_pnls)
@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_psr_monotonic_non_increasing_in_benchmark(pnls):
    """PSR(SR*) decrece (no crece) al subir el umbral SR*: exigir un Sharpe
    mayor solo puede bajar la probabilidad de superarlo. Se prueba cuando la
    entrada es estimable en los tres umbrales."""
    r_lo = metrics.calculate_psr(pnls, sr_benchmark=-0.5)
    r_mid = metrics.calculate_psr(pnls, sr_benchmark=0.0)
    r_hi = metrics.calculate_psr(pnls, sr_benchmark=0.5)
    if r_lo["available"] and r_mid["available"] and r_hi["available"]:
        assert r_hi["psr"] <= r_mid["psr"] <= r_lo["psr"]
