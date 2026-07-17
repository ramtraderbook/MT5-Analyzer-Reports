"""
test_prop_validator.py - Propiedades (Hypothesis) para validator.calculate_validator_score.

Capa P-B (property-based) del arnés de verdad ejecutable. Ningún test de este
archivo modifica validator.py: cuando Hypothesis encuentra un contraejemplo
real, el test se marca @pytest.mark.xfail(strict=True) con el repro mínimo en
lugar de debilitar la propiedad. Un xpass bajo strict=True es una falla del
arnés (significa que el defecto documentado se corrigió y el marcador quedó
obsoleto).

Anclas citadas contra el árbol de trabajo real (validator.py) y, cuando
aplica, contra docs/decision-logic.md y docs/known-issues.md -- nunca
contra "ground-truth.md" o "scratchpad/", que no existen en este repo.
"""

import copy
import math

import pytest
from hypothesis import HealthCheck, example, given, settings
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


@given(bt=bt_strategy(), live=live_strategy(), mc_r=clean_floats, mc_t=clean_floats, spp_med=clean_floats)
# Ambos overrides ocupan ventanas ANGOSTAS del espacio de entrada (dd_estado
# =="FUERA" y sobre todo pf_live<1.0 con tl>=50 -- un rango de 1.0 de ancho
# dentro de un dominio clean_floats de [-1e6,1e6]), así que la búsqueda
# aleatoria de Hypothesis por sí sola los visita con muy baja probabilidad
# dentro del presupuesto de max_examples. Verificado por mutation testing
# manual (pf_live<1.0 mutado a pf_live<1.3 en validator.py): la búsqueda
# aleatoria sin @example NO detectó la mutación en una corrida de 300
# ejemplos. Estos dos @example fijan un caso de cada rama de override
# explícitamente, así que la propiedad SIEMPRE los ejercita, en cada corrida,
# sin depender de qué ejemplos dibuje Hypothesis por azar.
@example(
    bt=make_bt(), live=make_live(total_trades=100, max_dd_pct=50.0),
    mc_r=12.0, mc_t=14.0, spp_med=10.0,
)  # dd_estado=="FUERA" (score alto, 79.0, que sin el override sería CONTINUAR)
@example(
    bt=make_bt(), live=make_live(total_trades=50, profit_factor=1.1),
    mc_r=12.0, mc_t=14.0, spp_med=10.0,
)  # pf_live<1.0 y tl>=50 (score alto, 77.9, que sin el override sería CONTINUAR)
@settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_verdict_band_function_is_total_partition(bt, live, mc_r, mc_t, spp_med, frozen_clock):
    """Totalidad/disjunción probada contra la CASCADA REAL de veredicto
    (validator.py:622-631), no contra una réplica.

    Una réplica anterior de este test modelaba solo la banda de score
    70/45 (CONFIG:40-41) e ignoraba las DOS ramas de override que la
    cascada real evalúa ANTES de mirar el score: dd_estado=="FUERA" (:622)
    y pf_live<1.0 con tl>=50 (:624). Como la réplica nunca llamaba a
    calculate_validator_score, un cambio real en esas dos ramas de
    override no la podía hacer fallar -- era una tautología. Esta versión
    llama a la función real y fija las dos ramas de override
    explícitamente junto con la banda de score, así que SÍ puede fallar
    si alguna de las tres cambia."""
    result = _call(bt, live, mc_r, mc_t, spp_med)

    if result["sin_datos"]:
        # Guard de datos mínimos (validator.py:191-259): no hay banda de
        # score que verificar, la cascada de veredicto ni se alcanza.
        assert result["veredicto"] in {"SIN DATOS", "ELIMINAR"}
        return

    veredicto = result["veredicto"]
    score = result["score"]
    dd_estado = result["dd_estado"]
    pf_live = result["pf_live"]
    tl = result["trades_live"]
    thresh_continuar = validator.CONFIG["thresh_continuar"]
    thresh_monitorear = validator.CONFIG["thresh_monitorear"]

    assert veredicto in {"CONTINUAR", "MONITOREAR", "ELIMINAR"}

    # Cascada real, en el mismo orden que validator.py:622-631: las dos
    # ramas de override ganan sobre la banda de score, exactamente una
    # rama aplica.
    if dd_estado == "FUERA":
        assert veredicto == "ELIMINAR"
    elif pf_live is not None and pf_live < 1.0 and tl >= 50:
        assert veredicto == "ELIMINAR"
    elif score >= thresh_continuar:
        assert veredicto == "CONTINUAR"
    elif score >= thresh_monitorear:
        assert veredicto == "MONITOREAR"
    else:
        assert veredicto == "ELIMINAR"


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
    100 por grupo pero nunca lo afirma en runtime. Fija esa invariante como
    test ejecutable contra el CONFIG real (no una copia)."""
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


# bt.trades_total y bt.months son los operandos de `bt_trades / bt_months`
# (validator.py:353). Dos floats PERFECTAMENTE NORMALES (ninguno subnormal) que
# pasan la guarda `> 0` pueden tener un cociente que hace underflow a 0.0
# exacto -- p.ej. 2.5e-238 / 4.6e+204 -> 0.0 -- y la división siguiente explota.
# Es una clase de defecto DISTINTA (y más amplia) que la del subnormal: acá el
# underflow lo produce la división, no la entrada, así que `allow_subnormal=False`
# no la evita. Se aísla en su propio test determinista
# (test_bt_freq_quotient_underflow_crashes, más abajo) y se acota acá para que
# esta propiedad general mida el espacio restante en vez de re-encontrar un
# defecto ya documentado.
_SAFE_BT_RATIO_OPERAND = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-10 ** 6, max_value=10 ** 6),
    st.floats(min_value=1e-6, max_value=1e6, allow_nan=False, allow_infinity=False),
    st.sampled_from(["", "5", "5.5", "-3", "abc", "1e10", "nan"]),
)


@st.composite
def fuzzy_bt(draw):
    bt = {
        k: draw(_SAFE_SCALARS)
        for k in (
            "win_rate", "profit_factor", "payout_ratio", "expectancy", "avg_bars",
            "max_dd_pct", "max_consec_losses",
            "worst_dd_1m", "worst_dd_3m", "stagnation_days",
        )
    }
    bt["trades_total"] = draw(_SAFE_BT_RATIO_OPERAND)
    bt["months"] = draw(_SAFE_BT_RATIO_OPERAND)
    return bt


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


# DETERMINISTIC, not @given: an earlier version of this test drew
# weeks_operating from st.floats(min_value=5e-324, max_value=2e-323) under
# xfail(strict=True). That range is NOT uniformly a counterexample --
# verified by direct execution against validator.calculate_validator_score:
# only k=1 (5e-324) and k=2 (1e-323) underflow `weeks_live / 4.33` (:434) to
# exactly 0.0 and crash; k=3 (1.5e-323) and k=4 (2e-323) round to a nonzero
# subnormal (5e-324) and DO NOT crash. Hypothesis choosing a non-crashing
# k inside that range would make xfail(strict=True) XPASS -- turning the
# whole suite red for the next person with zero code change, since nothing
# about the underlying defect changed. Pinning the exact two proven-crashing
# values makes the repro reproducible on every run instead of dependent on
# which example Hypothesis happens to draw.
@pytest.mark.parametrize("weeks", [5e-324, 1e-323], ids=["k=1", "k=2"])
@pytest.mark.xfail(strict=True, reason=(
    "COUNTEREXAMPLE (verified by direct execution, not just found once by "
    "Hypothesis): live={'weeks_operating': <float subnormal, 5e-324 or "
    "1e-323>} -> ZeroDivisionError not caught in validator.py:434 "
    "`live_freq_per_month = trades_live / (weeks_live / 4.33)`. weeks_live "
    "passes the guard `weeks_live > 0` (:432, both values are > 0) but "
    "`weeks_live / 4.33` UNDERFLOWS TO EXACTLY 0.0 for these two values only "
    "(the subnormal is too small to survive the 64-bit float division), and "
    "the following division by that 0.0 crashes. WHY ONLY k=1/k=2: larger "
    "subnormals (1.5e-323, 2e-323, ...) round DOWN to a nonzero subnormal "
    "(5e-324) instead of to 0.0, so they survive the division and do not "
    "crash -- verified: 1.5e-323/4.33 == 5e-324 != 0.0. Requires bt.trades_total "
    "and bt.months truthy (bt_trades and bt_months and bt_months>0) to reach "
    "that branch -- the default make_bt() satisfies this. Repro: "
    "calculate_validator_score(make_bt(), make_mc(), make_mc(), make_spp(), "
    "make_live(total_trades=5, weeks_operating=weeks))."
))
def test_weeks_operating_subnormal_crashes(weeks, frozen_clock):
    live = make_live(total_trades=5, weeks_operating=weeks)
    validator.calculate_validator_score(make_bt(), make_mc(), make_mc(), make_spp(), live)


# DETERMINISTIC, not @given: same rationale as test_weeks_operating_subnormal_crashes
# above. Verified by direct execution: for bt.trades_total / bt.months(=2.0),
# ONLY k=1 (5e-324) underflows to exactly 0.0 and crashes -- k=2 (1e-323)
# already rounds to a nonzero subnormal (5e-324) and does NOT crash (a
# narrower survival window than the /4.33 path above, because dividing by
# 2.0 loses one fewer bit of the subnormal than dividing by 4.33).
@pytest.mark.parametrize("trades_total", [5e-324], ids=["k=1"])
@pytest.mark.xfail(strict=True, reason=(
    "COUNTEREXAMPLE (verified by direct execution): bt={'trades_total': "
    "5e-324} -> ZeroDivisionError not caught in validator.py:354 "
    "`dd_limit = bt_worst_dd_1m * math.sqrt(trades_live / bt_freq_mes)`. "
    "bt_trades passes the guard `bt_trades > 0` (:341, 5e-324>0 is True) but "
    "`bt_freq_mes = bt_trades / bt_months` (:353, bt_months=2.0) UNDERFLOWS "
    "TO EXACTLY 0.0 for this one value only, and the following division by "
    "that 0.0 crashes. WHY ONLY k=1: 1e-323/2.0 == 5e-324 != 0.0 (verified), "
    "so it survives -- the /2.0 path has a narrower crash window than the "
    "/4.33 path in test_weeks_operating_subnormal_crashes (only k=1, not "
    "k=1 and k=2). Same underlying mechanism (a subnormal numerator "
    "survives a `>0` guard but not the intermediate division), a different "
    "bt field and code branch (scaled DD, not frequency). Repro: "
    "calculate_validator_score(make_bt(trades_total=5e-324, months=2.0), "
    "make_mc(), make_mc(), make_spp(), make_live(total_trades=5, "
    "weeks_operating=1.0))."
))
def test_bt_trades_total_subnormal_crashes(trades_total, frozen_clock):
    bt = make_bt(trades_total=trades_total, months=2.0)
    live = make_live(total_trades=5, weeks_operating=1.0)
    validator.calculate_validator_score(bt, make_mc(), make_mc(), make_spp(), live)


# DETERMINISTIC, not @given: Hypothesis encontró este caso con una semilla
# nueva, y se lo fija con el valor exacto para que no dependa del sorteo.
@pytest.mark.parametrize(
    "trades_total,months",
    [(2.48829416752246e-238, 4.605831368114236e+204), (1e-200, 1e200)],
    ids=["hypothesis-found", "hand-derived"],
)
@pytest.mark.xfail(strict=True, reason=(
    "COUNTEREXAMPLE (encontrado por Hypothesis con una semilla nueva, más "
    "amplio que el caso subnormal): dos floats PERFECTAMENTE NORMALES -- "
    "ninguno subnormal, ambos > 0 -- cuyo COCIENTE hace underflow a 0.0 "
    "exacto. bt.trades_total=2.49e-238 y bt.months=4.61e+204 pasan la guarda "
    "`bt_trades > 0 and bt_months > 0` (validator.py:341), pero "
    "`bt_freq_mes = bt_trades / bt_months` (:353) da EXACTAMENTE 0.0 porque "
    "el resultado cae por debajo del subnormal mínimo (~5e-324), y "
    "`trades_live / bt_freq_mes` (:354) explota con ZeroDivisionError. "
    "POR QUÉ IMPORTA MÁS QUE EL CASO SUBNORMAL: acá el underflow lo produce "
    "la DIVISIÓN, no la entrada, así que ninguna validación de rango sobre "
    "los operandos por separado lo evita -- `allow_subnormal=False` no "
    "ayuda. La guarda `x > 0 and y > 0` NO garantiza `x / y > 0`. "
    "Repro: calculate_validator_score(make_bt(trades_total=2.48829416752246e-238, "
    "months=4.605831368114236e+204), make_mc(), make_mc(), make_spp(), "
    "make_live(total_trades=5, weeks_operating=1.0))."
))
def test_bt_freq_quotient_underflow_crashes(trades_total, months, frozen_clock):
    bt = make_bt(trades_total=trades_total, months=months)
    live = make_live(total_trades=5, weeks_operating=1.0)
    validator.calculate_validator_score(bt, make_mc(), make_mc(), make_spp(), live)
