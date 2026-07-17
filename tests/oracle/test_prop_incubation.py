"""
test_prop_incubation.py - Propiedades (Hypothesis) para incubation_validator.py.

Capa P-B del arnés. Cubre `_score_metric`, `_resolve_cp3_verdict`,
`get_checkpoint_for_trades` y `evaluate_incubation`/`evaluate_cp3`. Igual que
en test_prop_validator.py: un contraejemplo real de Hypothesis se documenta
con @pytest.mark.xfail(strict=True) y el repro mínimo, nunca se debilita la
propiedad para forzarla a pasar.

Anclas contra el árbol de trabajo real (incubation_validator.py) y, cuando
aplica, contra docs/decision-logic.md -- nunca contra "ground-truth.md" o
"scratchpad/", que no existen en este repo.
"""

import ast
import copy
import inspect
import math

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import incubation_validator as iv
from conftest import make_reference, make_live_metrics


def _extract_dict_literal(func, var_name):
    """Extrae el dict literal asignado a `var_name` dentro del cuerpo FUENTE
    de `func`, via inspect.getsource + el módulo `ast` -- nunca ejecuta la
    función ni copia su expresión, solo parsea el AST y evalúa el literal
    encontrado con ast.literal_eval. Usado para leer pesos hardcodeados
    function-local (sin CONFIG importable) directamente de producción en vez
    de mantener una copia del test que nunca podría detectar un cambio real."""
    source = inspect.getsource(func)
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id == var_name and isinstance(node.value, ast.Dict):
                return ast.literal_eval(node.value)
    return None

clean_floats = st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False)
wide_floats = st.floats(min_value=-1e8, max_value=1e8, allow_nan=False, allow_infinity=False)


# ── 1. RANGO ──────────────────────────────────────────────────────────────────

@given(
    live=wide_floats, mc95=wide_floats, mc50=wide_floats, bt=wide_floats,
    higher=st.booleans(),
)
@settings(max_examples=1000, deadline=None)
def test_score_metric_range(live, mc95, mc50, bt, higher):
    """_score_metric (incubation_validator.py:784-813) siempre in [0,100],
    nunca NaN, para CUALQUIER combinación de referencias -- incluidas bandas
    invertidas (mc50 > bt, mc95 > mc50, etc.). El lower bound viene del
    `max(0.0, ...)` explícito (:805/:813); el upper bound es emergente: cada
    rama de la cascada if/elif sólo se alcanza cuando las ramas anteriores
    (más generosas) no aplicaron, así que ninguna rama puede superar 100."""
    score = iv._score_metric(live, mc95, mc50, bt, higher_is_better=higher)
    assert isinstance(score, float)
    assert not math.isnan(score)
    assert 0.0 <= score <= 100.0 + 1e-9


@given(
    wr=st.floats(min_value=1.0, max_value=100.0, allow_nan=False, allow_infinity=False),
    pf=st.floats(min_value=0.1, max_value=10.0, allow_nan=False, allow_infinity=False),
    exp=st.floats(min_value=-50.0, max_value=50.0, allow_nan=False, allow_infinity=False),
    payout=st.floats(min_value=0.1, max_value=10.0, allow_nan=False, allow_infinity=False),
    dd=st.floats(min_value=0.1, max_value=90.0, allow_nan=False, allow_infinity=False),
    stag=st.floats(min_value=0.0, max_value=300.0, allow_nan=False, allow_infinity=False),
    mcl=st.integers(min_value=0, max_value=50),
    total_trades=st.integers(min_value=40, max_value=200),
)
@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_cp3_final_score_and_category_scores_in_range(wr, pf, exp, payout, dd, stag, mcl, total_trades,
                                                        frozen_clock):
    """evaluate_cp3 -> score in [0,100] o None; cada category_scores[*]['score']
    también in [0,100] (incubation_validator.py:1116-1128)."""
    ref = make_reference()
    winning = int(round(total_trades * min(max(wr, 0.0), 100.0) / 100.0))
    live = make_live_metrics(
        total_trades=total_trades, winning_trades=winning, win_rate=wr,
        profit_factor=pf, expectancy=exp, avg_trade=exp, payout_ratio=payout,
        ret_dd=1.0, max_dd_pct=dd, max_consec_losses=mcl, stagnation_days=stag,
    )
    result = iv.evaluate_cp3(live, ref)

    score = result["score"]
    assert score is None or (isinstance(score, (int, float)) and not math.isnan(score) and 0.0 <= score <= 100.0)

    for cat, payload in result["category_scores"].items():
        cscore = payload["score"]
        assert not math.isnan(cscore)
        assert 0.0 <= cscore <= 100.0, (cat, cscore)


# ── 2. VEREDICTO: banda total y disjunta ─────────────────────────────────────

@given(
    score=st.floats(min_value=-100.0, max_value=200.0, allow_nan=False, allow_infinity=False),
    below_mc95=st.lists(st.sampled_from(["win_rate", "profit_factor", "max_dd_pct"]), max_size=3, unique=True),
    cp2_verdict=st.sampled_from([None, "CONTINUAR", "OBSERVAR", "ELIMINAR"]),
)
@settings(max_examples=300, deadline=None)
def test_resolve_cp3_verdict_is_total_partition(score, below_mc95, cp2_verdict):
    """_resolve_cp3_verdict (:816-842) es total: siempre devuelve uno de los
    tres veredictos legales, y la escalada CP2->CP3 sólo puede producir
    ELIMINAR cuando cp2_verdict=='OBSERVAR' Y el veredicto ya era OBSERVAR."""
    verdict, escalation = iv._resolve_cp3_verdict(score, below_mc95, cp2_verdict)

    assert verdict in {"APROBAR", "OBSERVAR", "ELIMINAR"}
    assert isinstance(escalation, bool)

    if escalation:
        assert verdict == "ELIMINAR"
        assert cp2_verdict == "OBSERVAR"

    if score >= 65 and not below_mc95 and cp2_verdict != "OBSERVAR":
        assert verdict == "APROBAR"
    if score < 45:
        assert verdict in {"OBSERVAR", "ELIMINAR"}  # OBSERVAR->ELIMINAR sólo si escaló; aquí basta el total


# ── 3. MONOTONICIDAD ─────────────────────────────────────────────────────────

@given(
    mc95=wide_floats, mc50=wide_floats, bt=wide_floats,
    live_a=wide_floats, live_b=wide_floats, higher=st.booleans(),
)
@settings(max_examples=3000, deadline=None)
def test_score_metric_monotonic_in_live_value(mc95, mc50, bt, live_a, live_b, higher):
    """Mejorar SOLO el valor live (con mc95/mc50/bt y la dirección fijos) nunca
    empeora _score_metric -- ni siquiera con bandas de referencia invertidas
    (mc50 > bt para higher_is_better, el escenario que la función de scoring
    de docs/decision-logic.md (sección "Función de scoring por métrica",
    CP3) señala como sospechoso por los guards `max(..., 0.001)`).

    Investigado a fondo (>25000 ejemplos de Hypothesis dirigidos, incluido un
    barrido específico con mc50>bt y higher=True): NO se encontró ningún
    contraejemplo. Razón estructural: cada rama de la cascada if/elif tiene
    pendiente no negativa en `live` (el numerador siempre tiene el signo de
    la rama y el `max(..., 0.001)` sólo puede forzar el denominador a ser
    positivo, nunca invertir su signo), y una banda invertida simplemente
    vuelve inalcanzable la rama intermedia correspondiente -- el valor cae en
    la rama MÁS generosa anterior, nunca en una peor. Por eso esta propiedad
    queda como aserción honesta (no xfail): la sospecha de banda invertida
    descrita arriba no se materializa para la monotonía en `live_value`.
    """
    lo, hi = sorted([live_a, live_b])
    s_lo = iv._score_metric(lo, mc95, mc50, bt, higher_is_better=higher)
    s_hi = iv._score_metric(hi, mc95, mc50, bt, higher_is_better=higher)

    if higher:
        assert s_lo <= s_hi + 1e-9
    else:
        assert s_hi <= s_lo + 1e-9


def test_cp3_score_display_matches_verdict_at_65_boundary(frozen_clock):
    """REGRESIÓN DE CONSISTENCIA (fix 4A, canonización sobre el valor publicado).

    Antes del fix esto era un contraejemplo confirmado: evaluate_cp3 decidía el
    veredicto con el `final_score` SIN redondear (:1165) mientras publicaba
    `round(final_score, 2)` (:1170), así que un crudo 64.998 daba OBSERVAR pero
    se mostraba 65.0 -- que el ojo lee como APROBAR. El repro se encontró por
    bisección analítica dirigida (la ventana de contradicción tiene ~1e-15 de
    ancho, inalcanzable para Hypothesis sobre floats genéricos), y se deja como
    test fijo para que el repro sea determinísticamente reproducible.

    Tras el fix, `evaluate_cp3` redondea UNA vez (`published_score`) y decide el
    veredicto con ese mismo valor. Con category_scores deviation=54.44
    (redondeado), risk=65.0, coherence=100.0, sample=60.0:

        raw final_score   = 0.45*54.44 + 0.30*65 + 0.15*100 + 0.10*60 = 64.998
        published_score   = round(64.998, 2) = 65.0  (>= 65 -> APROBAR)

    El usuario ahora ve `score: 65.0` junto a `verdict: 'APROBAR'`: el número
    mostrado y el veredicto salen del MISMO valor y no pueden contradecirse.

    Repro mínimo (live_metrics, reference_data = conftest.make_reference()):
        total_trades=45, winning_trades=22, win_rate=48.679375,
        profit_factor=1.2471750000000001, expectancy=avg_trade=7.207625,
        payout_ratio=1.0735875000000001, ret_dd=1.8679375, max_dd_pct=15.0,
        max_consec_losses=6, stagnation_days=45.0
    -> evaluate_cp3(...) == {"score": 65.0, "verdict": "OBSERVAR", ...}
    """
    ref = make_reference()
    live = make_live_metrics(
        total_trades=45, winning_trades=22,
        win_rate=48.679375,
        profit_factor=1.2471750000000001,
        expectancy=7.207625, avg_trade=7.207625,
        payout_ratio=1.0735875000000001,
        ret_dd=1.8679375,
        max_dd_pct=15.0, max_consec_losses=6, stagnation_days=45.0,
    )
    result = iv.evaluate_cp3(live, ref)

    # La propiedad HONESTA que debería cumplirse (y que el código viola):
    # un score mostrado >= 65 no debería poder coexistir con un veredicto
    # que no sea APROBAR (salvo por el gate below_mc95, que aquí no aplica --
    # todas las métricas están por encima de sus mc95 en este repro).
    assert not (result["score"] >= 65.0 and result["verdict"] != "APROBAR"), (
        f"contradicción confirmada: score={result['score']} verdict={result['verdict']}"
    )


# ── 4. DISPATCH DE CHECKPOINTS: partición total ──────────────────────────────

@given(n=st.integers(min_value=0, max_value=200))
@settings(max_examples=201, deadline=None)
def test_checkpoint_dispatch_is_total_partition(n):
    """get_checkpoint_for_trades (:306-313) cubre 0..200 sin huecos ni solapes,
    con las fronteras exactas de docs/decision-logic.md (sección "Sistema de
    checkpoints"): 4->PRE_CP1, 5->CP1, 19->CP1, 20->CP2, 39->CP2, 40->CP3."""
    checkpoint = iv.get_checkpoint_for_trades(n)
    assert checkpoint in {"PRE_CP1", "CP1", "CP2", "CP3"}

    branches = [
        (checkpoint == "PRE_CP1") == (n < 5),
        (checkpoint == "CP1") == (5 <= n < 20),
        (checkpoint == "CP2") == (20 <= n < 40),
        (checkpoint == "CP3") == (n >= 40),
    ]
    assert all(branches)


# ── 5. DETERMINISMO ──────────────────────────────────────────────────────────

@given(
    wr=st.floats(min_value=0.0, max_value=100.0, allow_nan=False, allow_infinity=False),
    pf=st.floats(min_value=0.0, max_value=10.0, allow_nan=False, allow_infinity=False),
    exp=st.floats(min_value=-50.0, max_value=50.0, allow_nan=False, allow_infinity=False),
    total_trades=st.integers(min_value=0, max_value=150),
)
@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_evaluate_incubation_is_deterministic(wr, pf, exp, total_trades, frozen_clock):
    """Misma entrada -> misma salida (dict deep-equal), llamando dos veces con
    copias independientes. El reloj está congelado (frozen_clock, autouse),
    así que hasta el campo `timestamp` coincide entre ambas llamadas."""
    ref = make_reference()
    live = make_live_metrics(total_trades=total_trades, win_rate=wr, profit_factor=pf, expectancy=exp, avg_trade=exp)

    r1 = iv.evaluate_incubation("EA", copy.deepcopy(live), copy.deepcopy(ref))
    r2 = iv.evaluate_incubation("EA", copy.deepcopy(live), copy.deepcopy(ref))
    assert r1 == r2


# ── 6. SUMA DE PESOS ──────────────────────────────────────────────────────────

def test_cp3_deviation_and_risk_sub_weights_sum_to_one():
    """incubation_validator.py:1036-1048 -- literales hardcodeados, sin CONFIG
    importable (verificado: no hay un dict CONFIG a nivel de módulo en
    incubation_validator.py, a diferencia de validator.CONFIG). No
    introspectables en runtime desde AFUERA del módulo (no son atributos de
    módulo, son dict literals locales dentro de evaluate_cp3), así que este
    test lee los literales REALES parseando el AST de la función real
    (inspect.getsource + ast, ver _extract_dict_literal arriba) en vez de
    mantener una copia hardcodeada.

    Una versión anterior de este test SÍ hardcodeaba su propia copia de
    estos pesos y nunca leía producción -- si alguien rebalanceaba los
    pesos reales en evaluate_cp3, el test seguía en verde porque comparaba
    la copia contra sí misma, no contra el código. Era una tautología. Esta
    versión falla si el AST no encuentra el dict (nombre de variable
    cambiado, o dejó de ser un literal) -- nunca pasa en silencio."""
    deviation_weights = _extract_dict_literal(iv.evaluate_cp3, "deviation_weights")
    risk_weights = _extract_dict_literal(iv.evaluate_cp3, "risk_weights")

    assert deviation_weights is not None, (
        "no se pudo extraer 'deviation_weights' del AST fuente de "
        "evaluate_cp3 -- ¿se renombró la variable o dejó de ser un dict "
        "literal en incubation_validator.py?"
    )
    assert risk_weights is not None, (
        "no se pudo extraer 'risk_weights' del AST fuente de evaluate_cp3 "
        "-- ¿se renombró la variable o dejó de ser un dict literal en "
        "incubation_validator.py?"
    )
    assert math.isclose(sum(deviation_weights.values()), 1.0)
    assert math.isclose(sum(risk_weights.values()), 1.0)


def test_cp3_category_weights_sum_to_one(frozen_clock):
    """category_scores[*]['weight'] SÍ se devuelve en la salida real de
    evaluate_cp3 (:1116-1121) -- a diferencia de los sub-pesos, este chequeo
    lee los pesos directamente del resultado real, no de una copia."""
    ref = make_reference()
    live = make_live_metrics(total_trades=90, winning_trades=50)
    result = iv.evaluate_cp3(live, ref)
    weights = [payload["weight"] for payload in result["category_scores"].values()]
    assert math.isclose(sum(weights), 1.0)


# ── 7. SIN EXCEPCIONES NO MANEJADAS ─────────────────────────────────────────

_SAFE_SCALARS = st.one_of(
    st.none(),
    st.booleans(),
    st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
    st.just(float("nan")),
    st.sampled_from(["", "5", "5.5", "-3", "∞", "-∞", "inf", "Infinity", "abc", "nan"]),
)

# total_trades/max_consec_losses pasan por `_safe_int` (:49-55, `_INT_PARSED_KEYS`),
# cuyo `round(float(...))` no captura OverflowError -- excluidos de este fuzz
# general para aislar ese defecto conocido en su propio test (más abajo).
_SAFE_INT_FIELD = st.one_of(
    st.none(),
    st.integers(min_value=0, max_value=500),
    st.floats(min_value=0.0, max_value=500.0, allow_nan=False, allow_infinity=False),
)


@st.composite
def fuzzy_live_metrics(draw):
    live = {
        # win_rate se excluye de aquí y se fija por separado: NaN + winning_trades
        # ausente es SU PROPIO contraejemplo confirmado (ver test más abajo);
        # mezclarlo en este fuzz general rompería el "no crash" honesto con un
        # defecto ya documentado en otro lado.
        k: draw(_SAFE_SCALARS)
        for k in ("profit_factor", "expectancy", "payout_ratio", "ret_dd",
                  "max_dd_pct", "stagnation_days", "avg_trade")
    }
    live["win_rate"] = draw(st.one_of(
        st.none(), st.booleans(),
        st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
        st.sampled_from(["", "5", "5.5", "-3", "∞", "-∞", "inf", "Infinity", "abc"]),
    ))
    live["total_trades"] = draw(_SAFE_INT_FIELD)
    # winning_trades se fuerza a estar SIEMPRE presente (no None) en este fuzz
    # general -- cuando está ausente, `_wins_from_metrics` (:118-125) deriva
    # wins desde win_rate, que es exactamente el camino del contraejemplo de
    # NaN de más abajo. Aquí se cubre la rama "winning_trades ya viene dado".
    live["winning_trades"] = draw(st.integers(min_value=0, max_value=500))
    live["max_consec_losses"] = draw(_SAFE_INT_FIELD)
    live["trades"] = []
    return live


@given(live=fuzzy_live_metrics())
@settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_no_crash_on_malformed_but_typed_live_metrics(live, frozen_clock):
    """Basura tipada (None/bool/float/strings numéricas y no numéricas) en
    cualquier campo float-parseado de live_metrics nunca debe crashear con
    reference_data completo -- debe degradar a SIN DATOS o a un veredicto
    normal, nunca lanzar una excepción. `winning_trades` se mantiene siempre
    presente aquí (ver el contraejemplo dedicado más abajo para el caso
    ausente + win_rate=NaN)."""
    ref = make_reference()
    result = iv.evaluate_incubation("EA", live, ref)
    assert isinstance(result, dict)
    assert "verdict" in result


@given(total_trades=st.integers(min_value=0, max_value=100))
@settings(max_examples=20, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@pytest.mark.xfail(strict=True, reason=(
    "COUNTEREXAMPLE: live_metrics sin 'winning_trades' y con win_rate=float('nan') "
    "-> ValueError no capturado. _wins_from_metrics (incubation_validator.py:118-125) "
    "sólo deriva wins desde win_rate cuando 'winning_trades' está ausente: "
    "`wr = _safe_float(live_metrics.get('win_rate'), 0.0) or 0.0` deja pasar NaN "
    "intacto (_safe_float no filtra NaN -- verificado directamente contra "
    "incubation_validator._safe_float), y "
    "`int(round(total * wr / 100.0))` explota con NaN. Repro mínimo: "
    "live = make_live_metrics(total_trades=5, win_rate=float('nan')); "
    "del live['winning_trades']; evaluate_incubation('EA', live, make_reference()) "
    "-> ValueError: cannot convert float NaN to integer."
))
def test_missing_winning_trades_with_nan_win_rate_crashes(total_trades, frozen_clock):
    ref = make_reference()
    live = make_live_metrics(total_trades=total_trades, win_rate=float("nan"))
    del live["winning_trades"]
    iv.evaluate_incubation("EA", live, ref)


@given(
    bad_date=st.one_of(st.text(max_size=20), st.none(), st.integers()),
    bad_period=st.text(max_size=30),
)
@settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_no_crash_on_malformed_reference_dates(bad_date, bad_period, frozen_clock):
    """`date_added` y `backtest.bt_period` malformados degradan a None/SIN
    DATOS vía `_parse_date_only` (:85-97, except ValueError) y
    `calculate_monthly_frequency` (:276-303, `.search` + except ValueError en
    el parseo de fecha) -- ambos `except ValueError` explícitos en el código
    fuente confirman que degradar a None/SIN DATOS es intencional, no un
    descuido. Nunca debe crashear."""
    ref = make_reference(date_added=bad_date)
    ref["backtest"] = dict(ref["backtest"], bt_period=bad_period)
    live = make_live_metrics(total_trades=10)
    result = iv.evaluate_incubation("EA", live, ref)
    assert isinstance(result, dict)


# DETERMINISTIC, not @given: the previous version declared
# @given(total_trades=st.integers(...)) but the body never used the drawn
# value -- it always called make_live_metrics(total_trades=float("inf")).
# Hypothesis was drawing 30 examples and re-running the exact same crash 30
# times under a different name each time; the strategy was dead weight that
# implied a property search without ever performing one. The counterexample
# IS a single fixed value (float('inf')), so this is a plain regression
# test under xfail(strict=True), consistent with the rest of this file's
# convention for documenting a confirmed defect without touching production.
@pytest.mark.xfail(strict=True, reason=(
    "COUNTEREXAMPLE: live_metrics['total_trades']=float('inf') -> OverflowError "
    "no capturado. incubation_validator._safe_int (:49-55) hace "
    "`int(round(float(str(value).replace(',', '.'))))`; para value=float('inf'), "
    "round(inf) lanza OverflowError, y el `except (TypeError, ValueError)` no lo "
    "cubre. total_trades y max_consec_losses son ambos _INT_PARSED_KEYS y "
    "comparten el mismo defecto -- reproducible en PRE_CP1/CP1/CP2/CP3 por igual. "
    "Repro mínimo: evaluate_incubation('EA', make_live_metrics(total_trades=float('inf')), "
    "make_reference()) -> OverflowError: cannot convert float infinity to integer."
))
def test_total_trades_infinity_crashes(frozen_clock):
    ref = make_reference()
    live = make_live_metrics(total_trades=float("inf"))
    iv.evaluate_incubation("EA", live, ref)


@given(mcl=st.sampled_from([float("inf"), float("-inf")]))
@settings(max_examples=10, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@pytest.mark.xfail(strict=True, reason=(
    "COUNTEREXAMPLE: live_metrics['max_consec_losses']=float('inf') -> OverflowError "
    "no capturado, mismo mecanismo que total_trades (incubation_validator.py:49-55, "
    "_safe_int no captura OverflowError). Repro mínimo: evaluate_incubation('EA', "
    "make_live_metrics(total_trades=50, max_consec_losses=float('inf')), "
    "make_reference())."
))
def test_max_consec_losses_infinity_crashes(mcl, frozen_clock):
    ref = make_reference()
    live = make_live_metrics(total_trades=50, max_consec_losses=mcl)
    iv.evaluate_incubation("EA", live, ref)
