"""
test_prop_validator.py - Propiedades (Hypothesis) para validator.calculate_validator_score.

Capa P-B (property-based) del arnés de verdad ejecutable. Ningún test de este
archivo modifica validator.py: cuando Hypothesis encuentra un contraejemplo
real, el test se marca @pytest.mark.xfail(strict=True) con el repro mínimo en
lugar de debilitar la propiedad. Un xpass bajo strict=True es una falla del
arnés (significa que el defecto documentado se corrigió y el marcador quedó
obsoleto).

Anclas citadas contra el árbol de trabajo en commit a934bcc (ver
scratchpad/ground-truth.md).
"""

import copy
import math

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import validator
from conftest import make_bt, make_live, make_mc, make_spp

# ── Estrategias reutilizables ────────────────────────────────────────────────

# Floats "limpios": sin NaN/inf/subnormales, para las propiedades de rango/
# monotonía/determinismo, donde el objetivo es la aritmética del validador,
# no sus guardas de entrada malformada (esas se cubren aparte, en las
# propiedades 7). allow_subnormal=False excluye 0 < |x| < ~2.2e-308: un
# bt.trades_total o live.weeks_operating subnormal pasa una guarda `> 0`
# pero underflowea a 0.0 exacto en una división intermedia (bt_trades/
# bt_months en :354 y :433, weeks_live/4.33 en :434), y la división
# siguiente por ese 0.0 crashea -- son contraejemplos reales, documentados
# en sus propios tests xfail más abajo, no ruido de esta propiedad general.
clean_floats = st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False,
                          allow_subnormal=False)
small_nonneg_int = st.integers(min_value=0, max_value=1000)
# allow_subnormal=False: excluye el rango subnormal (0 < x < ~2.2e-308). Un
# weeks_operating subnormal (p.ej. 5e-324) pasa la guarda `weeks_live > 0`
# (:432) pero `weeks_live / 4.33` (:434) HACE UNDERFLOW A 0.0 exacto, y la
# división siguiente crashea -- es un contraejemplo real y documentado, no
# ruido: ver test_weeks_operating_subnormal_crashes más abajo. Se excluye
# aquí para mantener honestas las propiedades de rango/veredicto/determinismo,
# que no apuntan a ese defecto específico.
small_nonneg_float = st.floats(min_value=0.0, max_value=500.0, allow_nan=False, allow_infinity=False,
                                allow_subnormal=False)

@st.composite
def bt_strategy(draw):
    """Backtest de referencia con todos los campos leídos por el validador (:113-124)."""
    return make_bt(
        win_rate=draw(clean_floats),
        profit_factor=draw(clean_floats),
        payout_ratio=draw(clean_floats),
        expectancy=draw(clean_floats),
        avg_bars=draw(clean_floats),
        max_dd_pct=draw(clean_floats),
        max_consec_losses=draw(clean_floats),
        trades_total=draw(clean_floats),
        months=draw(clean_floats),
        worst_dd_1m=draw(clean_floats),
        worst_dd_3m=draw(clean_floats),
        stagnation_days=draw(clean_floats),
    )


@st.composite
def live_strategy(draw):
    """Datos live con todos los campos leídos por el validador (:99-110)."""
    return make_live(
        total_trades=draw(small_nonneg_int),
        weeks_operating=draw(small_nonneg_float),
        win_rate=draw(clean_floats),
        profit_factor=draw(clean_floats),
        payout_ratio=draw(clean_floats),
        expectancy=draw(clean_floats),
        max_dd_pct=draw(clean_floats),
        avg_bars_live=draw(clean_floats),
        max_consec_losses=draw(clean_floats),
        stagnation_days=draw(clean_floats),
    )


def _call(bt, live, mc_r, mc_t, spp_med):
    return validator.calculate_validator_score(bt, make_mc(mc_r), make_mc(mc_t), make_spp(spp_med), live)


# ── 1. RANGO ──────────────────────────────────────────────────────────────────

@given(bt=bt_strategy(), live=live_strategy(), mc_r=clean_floats, mc_t=clean_floats, spp_med=clean_floats)
@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_score_and_subscores_always_in_range(bt, live, mc_r, mc_t, spp_med, frozen_clock):
    """score in [0,100] o None; s_riesgo/s_edge/s_caracter in [0,10] o None; s_desv in {10,8,5,0} o None.

    validator.py:608-614 nunca clampea `score` -- el rango es una consecuencia
    aritmética de que `_pts` in {0,5,10} y los sub-pesos suman 100 por
    categoría (CONFIG :22-38). Esta propiedad fija esa consecuencia como
    invariante ejecutable.
    """
    result = _call(bt, live, mc_r, mc_t, spp_med)

    score = result["score"]
    assert score is None or (isinstance(score, (int, float)) and not math.isnan(score) and 0.0 <= score <= 100.0)

    for key in ("s_riesgo", "s_edge", "s_caracter"):
        v = result[key]
        assert v is None or (isinstance(v, (int, float)) and not math.isnan(v) and 0.0 <= v <= 10.0)

    assert result["s_desv"] in (None, 10, 8, 5, 0)


# ── 2. VEREDICTO: banda total y disjunta ─────────────────────────────────────

@given(bt=bt_strategy(), live=live_strategy(), mc_r=clean_floats, mc_t=clean_floats, spp_med=clean_floats)
@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_veredicto_always_in_legal_set(bt, live, mc_r, mc_t, spp_med, frozen_clock):
    """veredicto in {CONTINUAR, MONITOREAR, ELIMINAR, SIN DATOS} para cualquier entrada."""
    result = _call(bt, live, mc_r, mc_t, spp_med)
    assert result["veredicto"] in {"CONTINUAR", "MONITOREAR", "ELIMINAR", "SIN DATOS"}
    assert result["sin_datos"] is (result["veredicto"] == "SIN DATOS") or result["veredicto"] == "ELIMINAR"


def _veredicto_from_score(score, thresh_continuar, thresh_monitorear):
    """Réplica pura de la cascada validator.py:626-631 (ignora los atajos de
    dd_estado/pf_live -- esos se prueban aparte, aquí se fija sólo la
    partición score->veredicto en sí)."""
    if score >= thresh_continuar:
        return "CONTINUAR"
    elif score >= thresh_monitorear:
        return "MONITOREAR"
    else:
        return "ELIMINAR"


@given(score=st.floats(min_value=-1000, max_value=1000, allow_nan=False, allow_infinity=False))
@settings(max_examples=300, deadline=None)
def test_verdict_band_function_is_total_partition(score):
    """La cascada de bandas 70/45 (CONFIG:40-41) es total y disjunta: exactamente
    una rama aplica para cualquier score, sin huecos ni solapes."""
    thresh_continuar = validator.CONFIG["thresh_continuar"]
    thresh_monitorear = validator.CONFIG["thresh_monitorear"]

    veredicto = _veredicto_from_score(score, thresh_continuar, thresh_monitorear)
    assert veredicto in {"CONTINUAR", "MONITOREAR", "ELIMINAR"}

    branches = [
        score >= thresh_continuar,
        thresh_monitorear <= score < thresh_continuar,
        score < thresh_monitorear,
    ]
    assert sum(branches) == 1


# ── 3. MONOTONICIDAD ─────────────────────────────────────────────────────────

@given(
    wr_worse=st.floats(min_value=0.0, max_value=55.0, allow_nan=False, allow_infinity=False),
    wr_better=st.floats(min_value=0.0, max_value=55.0, allow_nan=False, allow_infinity=False),
)
@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_score_monotone_as_win_rate_estado_improves(wr_worse, wr_better, frozen_clock):
    """Mejorar SOLO wr_estado (FUERA->ALERTA->OK), con todo lo demás fijo en la
    fixture sana, nunca puede empeorar el score total.

    Restringido a wr in [0, bt_wr=55]: por debajo de bt_wr tanto abs(wr_delta)
    (banda wr_estado, :265-272) como la condición de detcount `wr_live <
    bt_wr - 5` (:578, que alimenta s_desv) mejoran monótonamente al acercarse
    a bt_wr desde abajo -- evita el falso contraejemplo de usar distancia
    absoluta con wr por ENCIMA de bt_wr, donde detcount es asimétrico
    (sólo dispara por debajo) y rompería esta propiedad sin ser un bug real.
    """
    lo, hi = sorted([wr_worse, wr_better])  # lo = más lejos de bt_wr = peor

    bt = make_bt()  # bt_wr = 55.0 fijo
    live_lo = make_live(win_rate=lo)
    live_hi = make_live(win_rate=hi)

    r_lo = validator.calculate_validator_score(bt, make_mc(), make_mc(), make_spp(), live_lo)
    r_hi = validator.calculate_validator_score(bt, make_mc(), make_mc(), make_spp(), live_hi)

    assert not r_lo["sin_datos"] and not r_hi["sin_datos"]
    assert r_lo["score"] <= r_hi["score"] + 1e-9


# ── 4. (dispatch de checkpoints no aplica a validator.py; ver test_prop_incubation.py) ──

# ── 5. DETERMINISMO ──────────────────────────────────────────────────────────

@given(bt=bt_strategy(), live=live_strategy(), mc_r=clean_floats, mc_t=clean_floats, spp_med=clean_floats)
@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_calculate_validator_score_is_deterministic(bt, live, mc_r, mc_t, spp_med, frozen_clock):
    """Misma entrada -> misma salida, llamando dos veces (reloj congelado por frozen_clock)."""
    bt2, live2 = copy.deepcopy(bt), copy.deepcopy(live)
    r1 = _call(bt, live, mc_r, mc_t, spp_med)
    r2 = _call(bt2, live2, mc_r, mc_t, spp_med)
    assert r1 == r2


# ── 6. SUMA DE PESOS ──────────────────────────────────────────────────────────

def test_config_weights_sum_to_declared_totals():
    """CONFIG (validator.py:18-50) declara en comentarios que los pesos suman
    100 por grupo pero nunca lo afirma en runtime (ground-truth §4.1). Fija
    esa invariante como test ejecutable contra el CONFIG real (no una copia)."""
    cfg = validator.CONFIG
    assert cfg["w_riesgo"] + cfg["w_edge"] + cfg["w_caracter"] + cfg["w_desv"] == 100
    assert cfg["w_dd_escalado"] + cfg["w_consec_losses"] + cfg["w_stagnation"] == 100
    assert cfg["w_win_rate"] + cfg["w_profit_factor"] + cfg["w_payout_ratio"] + cfg["w_edge_erosion"] == 100
    assert cfg["w_frecuencia"] + cfg["w_avg_bars"] == 100


# ── 7. SIN EXCEPCIONES NO MANEJADAS ─────────────────────────────────────────

_SAFE_SCALARS = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-10 ** 6, max_value=10 ** 6),
    st.floats(allow_nan=True, allow_infinity=True, width=64),
    st.sampled_from(["", "5", "5.5", "-3", "∞", "-∞", "inf", "Infinity", "abc", "1e10", "nan"]),
)

# total_trades / weeks_operating pasan por `float(x or 0)` (validator.py:99-100),
# NO por _safe_float -- restringidos aquí a tipos seguros para aislar ese
# defecto conocido en su propio test (más abajo), en vez de ensuciar esta
# propiedad general de "no explota con basura tipada".
_SAFE_TRADE_COUNT = st.one_of(
    st.none(),
    st.integers(min_value=0, max_value=5000),
    # allow_subnormal=False: ver la nota sobre small_nonneg_float más arriba --
    # un weeks_operating subnormal crashea en :434 por una razón distinta
    # (aislada en su propio test), no por el defecto que cubre esta propiedad.
    st.floats(min_value=0.0, max_value=5000.0, allow_nan=False, allow_infinity=False, allow_subnormal=False),
)


@st.composite
def fuzzy_bt(draw):
    return {
        k: draw(_SAFE_SCALARS)
        for k in (
            "win_rate", "profit_factor", "payout_ratio", "expectancy", "avg_bars",
            "max_dd_pct", "max_consec_losses", "trades_total", "months",
            "worst_dd_1m", "worst_dd_3m", "stagnation_days",
        )
    }


@st.composite
def fuzzy_live(draw):
    live = {
        k: draw(_SAFE_SCALARS)
        for k in (
            "win_rate", "profit_factor", "payout_ratio", "expectancy", "max_dd_pct",
            "avg_bars_live", "max_consec_losses", "stagnation_days",
        )
    }
    live["total_trades"] = draw(_SAFE_TRADE_COUNT)
    live["weeks_operating"] = draw(_SAFE_TRADE_COUNT)
    return live


@given(
    bt=fuzzy_bt(), live=fuzzy_live(),
    mc_r=_SAFE_SCALARS, mc_t=_SAFE_SCALARS, spp_med=_SAFE_SCALARS,
)
@settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_no_crash_on_malformed_but_typed_input(bt, live, mc_r, mc_t, spp_med, frozen_clock):
    """Basura tipada (None/bool/int/float con NaN o inf/strings numéricas y no
    numéricas) en cualquier campo que pase por `_safe_float` (:692-713) nunca
    debe tirar una excepción -- debe degradar a SIN DATOS o a un estado N/D
    parcial, nunca crashear. total_trades/weeks_operating quedan fuera (ver
    los dos tests siguientes: ésos SÍ crashean, es un defecto real distinto)."""
    result = validator.calculate_validator_score(
        bt, {"max_dd": mc_r}, {"max_dd": mc_t}, {"expectancy_median": spp_med}, live
    )
    assert isinstance(result, dict)
    assert result["sin_datos"] in (True, False)


def _is_bad_numeric_string(s):
    """True si `s` NO es convertible por float() y no es falsy (una cadena
    vacía se vuelve 0 vía `x or 0` y no crashea -- no es un contraejemplo)."""
    if not s:
        return False
    try:
        float(s)
        return False
    except ValueError:
        return True


bad_numeric_text = st.text(min_size=1, max_size=20).filter(_is_bad_numeric_string)


@given(bad=bad_numeric_text)
@settings(max_examples=50, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@pytest.mark.xfail(strict=True, reason=(
    "COUNTEREXAMPLE: live={'total_trades': '<cadena no numérica>'} -> ValueError "
    "no capturado en validator.py:99 `float(live.get('total_trades') or 0)` "
    "(este campo NO pasa por _safe_float, a diferencia de los otros 9 campos "
    "de `live`). Repro mínimo: calculate_validator_score(make_bt(), make_mc(), "
    "make_mc(), make_spp(), {'total_trades': 'abc', ...}) "
    "-> ValueError: could not convert string to float: 'abc'. "
    "Mismo defecto en validator.py:100 para weeks_operating (probado aquí también)."
))
def test_total_trades_non_numeric_string_crashes(bad, frozen_clock):
    live = make_live(total_trades=bad)
    validator.calculate_validator_score(make_bt(), make_mc(), make_mc(), make_spp(), live)


@given(bad=bad_numeric_text)
@settings(max_examples=50, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@pytest.mark.xfail(strict=True, reason=(
    "COUNTEREXAMPLE: live={'weeks_operating': '<cadena no numérica>'} -> ValueError "
    "no capturado en validator.py:100 `float(live.get('weeks_operating') or 0)`. "
    "Repro mínimo: calculate_validator_score(make_bt(), make_mc(), make_mc(), "
    "make_spp(), {'total_trades': 10, 'weeks_operating': 'xyz'}) "
    "-> ValueError: could not convert string to float: 'xyz'."
))
def test_weeks_operating_non_numeric_string_crashes(bad, frozen_clock):
    live = make_live(total_trades=10, weeks_operating=bad)
    validator.calculate_validator_score(make_bt(), make_mc(), make_mc(), make_spp(), live)


@given(bad=st.sampled_from([float("nan"), float("inf"), float("-inf")]))
@settings(max_examples=10, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@pytest.mark.xfail(strict=True, reason=(
    "COUNTEREXAMPLE: live={'total_trades': float('nan')} -> ValueError "
    "'cannot convert float NaN to integer' en validator.py:134 `tl = int(trades_live)`; "
    "float('inf')/float('-inf') -> OverflowError en la misma línea. Ninguno de "
    "los dos pasa por _safe_float (que sí maneja NaN/inf con gracia, :692-713) "
    "porque total_trades usa `float(live.get(...) or 0)` directo (:99). "
    "Repro mínimo: calculate_validator_score(make_bt(), make_mc(), make_mc(), "
    "make_spp(), {'total_trades': float('nan')})."
))
def test_total_trades_nan_or_inf_crashes(bad, frozen_clock):
    live = make_live(total_trades=bad)
    validator.calculate_validator_score(make_bt(), make_mc(), make_mc(), make_spp(), live)


@given(weeks=st.floats(min_value=5e-324, max_value=2e-323, allow_nan=False, allow_infinity=False))
@settings(max_examples=30, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@pytest.mark.xfail(strict=True, reason=(
    "COUNTEREXAMPLE (encontrado por Hypothesis, no previsto en el ground-truth): "
    "live={'weeks_operating': <float subnormal, p.ej. 5e-324>} -> ZeroDivisionError "
    "no capturado en validator.py:434 `live_freq_per_month = trades_live / "
    "(weeks_live / 4.33)`. weeks_live pasa la guarda `weeks_live > 0` (:432, "
    "5e-324 > 0 es True) pero `weeks_live / 4.33` HACE UNDERFLOW A 0.0 exacto "
    "(el subnormal es demasiado chico para sobrevivir la división en punto "
    "flotante de 64 bits), y la división siguiente por ese 0.0 crashea. "
    "Requiere además bt.trades_total y bt.months truthy (bt_trades and "
    "bt_months and bt_months>0) para entrar a esa rama -- con bt completo "
    "(make_bt() por defecto) se reproduce siempre. Repro mínimo: "
    "calculate_validator_score(make_bt(), make_mc(), make_mc(), make_spp(), "
    "make_live(total_trades=5, weeks_operating=5e-324))."
))
def test_weeks_operating_subnormal_crashes(weeks, frozen_clock):
    live = make_live(total_trades=5, weeks_operating=weeks)
    validator.calculate_validator_score(make_bt(), make_mc(), make_mc(), make_spp(), live)


@given(trades_total=st.floats(min_value=5e-324, max_value=2e-323, allow_nan=False, allow_infinity=False))
@settings(max_examples=30, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@pytest.mark.xfail(strict=True, reason=(
    "COUNTEREXAMPLE (encontrado por Hypothesis): bt={'trades_total': <float "
    "subnormal, p.ej. 5e-324>} -> ZeroDivisionError no capturado en "
    "validator.py:354 `dd_limit = bt_worst_dd_1m * math.sqrt(trades_live / "
    "bt_freq_mes)`. bt_trades pasa la guarda `bt_trades > 0` (:341, 5e-324>0 "
    "es True) pero `bt_freq_mes = bt_trades / bt_months` (:353) HACE "
    "UNDERFLOW A 0.0 exacto, y la división siguiente por ese 0.0 crashea. "
    "Mismo mecanismo de fondo que test_weeks_operating_subnormal_crashes "
    "(un numerador subnormal sobrevive una guarda `>0` pero no sobrevive la "
    "división intermedia), disparado por un campo bt distinto y una rama de "
    "código distinta (DD escalado, no frecuencia). Repro mínimo: "
    "calculate_validator_score(make_bt(trades_total=5e-324, months=2.0), "
    "make_mc(), make_mc(), make_spp(), make_live(total_trades=5, "
    "weeks_operating=1.0))."
))
def test_bt_trades_total_subnormal_crashes(trades_total, frozen_clock):
    bt = make_bt(trades_total=trades_total, months=2.0)
    live = make_live(total_trades=5, weeks_operating=1.0)
    validator.calculate_validator_score(bt, make_mc(), make_mc(), make_spp(), live)
