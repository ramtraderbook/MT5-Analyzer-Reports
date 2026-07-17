"""
test_char_incubation.py - Caracterizacion de incubation_validator.py (capa 1,
harness P-A).

Fija el comportamiento ACTUAL de evaluate_incubation/evaluate_cp1/cp2/cp3,
_hard_gates y get_checkpoint_for_trades -- lo que HAY, no lo que DEBERIA
haber. Los defectos conocidos se marcan con DEFECT-PIN. Todas las entradas
son hardcodeadas via los helpers de conftest.py; nada se lee de disco.

Todos los valores numericos criticos (bordes de p-valor binomial, bandas de
CP3 continuas, el split redondeado-vs-crudo de CP3) fueron obtenidos por
busqueda/biseccion contra la funcion real antes de hardcodear el assert --
ver el reporte de la tarea para el detalle de cada busqueda.
"""

import copy

import pytest

import incubation_validator as iv
from conftest import make_reference, make_live_metrics, make_mc_level, make_mc_block


# ── §5: dispatch de checkpoint por cantidad de trades ───────────────────────

@pytest.mark.parametrize("n,expected", [
    (4, "PRE_CP1"), (5, "CP1"), (19, "CP1"), (20, "CP2"), (39, "CP2"), (40, "CP3"),
])
def test_checkpoint_dispatch_boundaries(n, expected):
    assert iv.get_checkpoint_for_trades(n) == expected


# ── §3.15: _hard_gates ──────────────────────────────────────────────────────

def _ref_for_gates(**over):
    ref = {
        "date_added": "2026-01-02",
        "backtest": dict(win_rate=50.0, total_trades=20, bt_period="2025.01.01 - 2025.12.31"),
        "mc_manipulation": {"confidence_95": dict(max_dd_pct=20.0, max_consec_losses=8)},
        "mc_retest": {"confidence_95": dict(max_dd_pct=20.0, max_consec_losses=8)},
    }
    ref.update(over)
    return ref


def test_hard_gate_dd_extreme_1_5x_boundary():
    """threshold = mc95_dd * 1.5 = 20*1.5 = 30.0 exacto; passed = live_dd<=threshold."""
    lm = dict(total_trades=20, winning_trades=11, win_rate=55.0, max_dd_pct=30.0, max_consec_losses=2)
    g = iv._hard_gates(lm, _ref_for_gates())
    assert g["dd_extreme"] == {"passed": True, "live_value": 30.0, "threshold": 30.0}

    lm2 = dict(lm, max_dd_pct=30.01)
    g2 = iv._hard_gates(lm2, _ref_for_gates())
    assert g2["dd_extreme"]["passed"] is False


def test_hard_gate_win_rate_binomial_p_value_0_03_boundary():
    """
    _binomial_p_value(wins,n=20,p=0.5): en wins=5 el p-valor es 0.020695
    (<0.03, falla); en wins=6 es 0.057659 (>=0.03, pasa). Umbral verbatim
    incubation_validator.py:346.
    """
    ref = _ref_for_gates(backtest=dict(win_rate=50.0, total_trades=20,
                                        bt_period="2025.01.01 - 2025.12.31"))
    lm_fail = dict(total_trades=20, winning_trades=5, win_rate=25.0, max_dd_pct=5.0, max_consec_losses=2)
    g_fail = iv._hard_gates(lm_fail, ref)
    assert round(g_fail["win_rate_binomial"]["p_value"], 6) == 0.020695
    assert g_fail["win_rate_binomial"]["passed"] is False

    lm_pass = dict(total_trades=20, winning_trades=6, win_rate=30.0, max_dd_pct=5.0, max_consec_losses=2)
    g_pass = iv._hard_gates(lm_pass, ref)
    assert round(g_pass["win_rate_binomial"]["p_value"], 6) == 0.057659
    assert g_pass["win_rate_binomial"]["passed"] is True


def test_hard_gate_max_consec_losses_le():
    ref = _ref_for_gates()
    lm_ok = dict(total_trades=20, winning_trades=11, win_rate=55.0, max_dd_pct=5.0, max_consec_losses=8)
    g_ok = iv._hard_gates(lm_ok, ref)
    assert g_ok["max_consec_losses"] == {"passed": True, "live_value": 8, "mc95_value": 8}

    lm_fail = dict(lm_ok, max_consec_losses=9)
    g_fail = iv._hard_gates(lm_fail, ref)
    assert g_fail["max_consec_losses"]["passed"] is False


def test_hard_gate_frequency_expected_monthly_zero_is_silently_ok():
    """
    DEFECT-PIN: `_hard_gates` (incubation_validator.py:333) resuelve
    bt_total via `_safe_int(backtest.total_trades, 0) or total_trades` -- si
    backtest.total_trades es 0/ausente, se sustituye SILENCIOSAMENTE por el
    conteo de trades LIVE. Con total_trades live tambien en 0,
    calculate_monthly_frequency(0, periodo_valido) devuelve 0.0 (no None).
    El chequeo de warning es `elif expected_monthly>0`, asi que
    expected_monthly==0.0 no cae ni en la rama "SIN DATOS" (is None) ni en
    la rama de warning (>0) -- freq_warning queda en su default "OK",
    disfrazando una referencia de frecuencia completamente vacia como una
    frecuencia sana. Pinneado porque es el comportamiento actual
    (incubation_validator.py:333, 361-368), NO porque sea correcto.
    """
    lm = dict(total_trades=0, winning_trades=0, win_rate=0.0, max_dd_pct=5.0, max_consec_losses=2)
    ref = _ref_for_gates(backtest=dict(win_rate=55.0, total_trades=0,
                                        bt_period="2025.01.01 - 2025.12.31"))
    g = iv._hard_gates(lm, ref)
    assert g["frequency"] == {"status": "OK", "expected": 0.0, "actual": 0.0}


def test_hard_gate_frequency_sin_datos_when_period_unparseable():
    lm = dict(total_trades=10, winning_trades=5, win_rate=50.0, max_dd_pct=5.0, max_consec_losses=2)
    ref = _ref_for_gates(backtest=dict(win_rate=55.0, total_trades=10, bt_period=""))
    g = iv._hard_gates(lm, ref)
    assert g["frequency"]["status"] == "SIN DATOS"
    assert g["frequency"]["expected"] is None


def test_hard_gate_frequency_warning_ratio_outside_0_25_3_0():
    """ratio<0.25 o >3.0 -> WARNING (incubation_validator.py:361-368)."""
    lm = dict(total_trades=1, winning_trades=1, win_rate=100.0, max_dd_pct=5.0, max_consec_losses=2)
    ref = _ref_for_gates(
        date_added="2025-01-01",  # muy antiguo -> pocos trades/mes reales vs BT
        backtest=dict(win_rate=55.0, total_trades=200, bt_period="2025.01.01 - 2025.12.31"),
    )
    g = iv._hard_gates(lm, ref)
    assert g["frequency"]["status"] == "WARNING"


# ── §1.3 bold: forma SIN DATOS vs exito de evaluate_cp1 -- claves incompatibles ──

def test_cp1_success_and_sin_datos_shapes_have_incompatible_key_sets():
    """
    DEFECT-PIN: evaluate_cp1's "exito" shape (:586) solo tiene 6 claves
    (checkpoint, verdict, score, gates, hard_gate_failures, mc_source);
    _sd_result() (:475, camino SIN DATOS) devuelve un superset de 13 claves
    con nombres DISTINTOS de proposito (metrics_evaluation, metrics_scores,
    category_scores, spp_adjustments, escalation_from_cp2, sin_datos,
    missing) que la forma de exito JAMAS produce. Un consumidor que
    confia en un conjunto de claves fijo para CP1 se rompe al alternar
    entre ambas formas. Pinneado como comportamiento actual
    (incubation_validator.py:475-491 vs 586-593), NO como diseño correcto.
    """
    ref_ok = make_reference()
    lm = make_live_metrics(total_trades=10, winning_trades=6)
    r_ok = iv.evaluate_cp1(lm, ref_ok)
    assert set(r_ok.keys()) == {
        "checkpoint", "verdict", "score", "gates", "hard_gate_failures", "mc_source",
    }

    r_sd = iv.evaluate_cp1(lm, {})
    assert set(r_sd.keys()) == {
        "checkpoint", "verdict", "score", "sin_datos", "missing", "gates",
        "hard_gate_failures", "metrics_evaluation", "metrics_scores",
        "category_scores", "spp_adjustments", "escalation_from_cp2", "mc_source",
    }
    assert r_sd["verdict"] == "SIN DATOS"
    assert r_sd["sin_datos"] is True
    # 6 claves vs 13: un consumidor que fija su parser al shape de exito no
    # puede leer "sin_datos"/"missing" de la forma SIN DATOS, y uno que fija
    # su parser al shape SIN DATOS encuentra "sin_datos"/"missing" ausentes
    # (KeyError) en la forma de exito -- son shapes incompatibles, no solo
    # de distinto tamano.
    assert len(r_ok) == 6
    assert len(r_sd) == 13
    assert r_ok.keys() != r_sd.keys()
    assert "sin_datos" not in r_ok
    assert "missing" not in r_ok


# ── §3.16: CP1 verdict ──────────────────────────────────────────────────────

def test_cp1_verdict_continuar_when_all_gates_pass():
    ref = make_reference()
    lm = make_live_metrics(total_trades=10, winning_trades=6)
    r = iv.evaluate_cp1(lm, ref)
    assert r["hard_gate_failures"] == []
    assert r["verdict"] == "CONTINUAR"
    assert r["score"] is None  # CP1 score is siempre None (§2)


def test_cp1_verdict_eliminar_when_any_gate_fails():
    ref = make_reference()
    # dd extremo: mc95 default max_dd_pct=20 -> threshold=30; live 50 falla
    lm = make_live_metrics(total_trades=10, winning_trades=6, max_dd_pct=50.0)
    r = iv.evaluate_cp1(lm, ref)
    assert "dd_extreme" in r["hard_gate_failures"]
    assert r["verdict"] == "ELIMINAR"


# ── §3.17: CP2 status por metrica + verdict por failing_count ──────────────

def test_cp2_metric_status_good_acceptable_failing():
    assert iv._metric_status(60.0, 50.0, 40.0, higher_is_better=True) == "good"       # >=mc50
    assert iv._metric_status(45.0, 50.0, 40.0, higher_is_better=True) == "acceptable"  # >=mc95,<mc50
    assert iv._metric_status(35.0, 50.0, 40.0, higher_is_better=True) == "failing"     # <mc95
    assert iv._metric_status(None, 50.0, 40.0, higher_is_better=True) == "failing"     # None -> failing


def test_cp2_failing_count_verdict_boundaries():
    ref = make_reference()
    # baseline sano: 0 failing -> CONTINUAR
    lm0 = make_live_metrics(total_trades=30, winning_trades=17)
    r0 = iv.evaluate_cp2(lm0, ref)
    assert r0["hard_gate_failures"] == []
    assert r0["failing_count"] == 0
    assert r0["verdict"] == "CONTINUAR"

    # win_rate solo (mc95 default win_rate=45.0) -> failing_count=1 -> CONTINUAR
    lm1 = make_live_metrics(total_trades=30, winning_trades=15, win_rate=44.0)
    r1 = iv.evaluate_cp2(lm1, ref)
    assert r1["hard_gate_failures"] == []
    assert r1["failing_count"] == 1
    assert r1["verdict"] == "CONTINUAR"

    # expectancy degradada afecta "expectancy" Y "avg_trade" a la vez (comparten
    # el mismo valor live) -> failing_count salta de 0 a 2 en un solo cambio
    lm2 = make_live_metrics(total_trades=30, winning_trades=17, expectancy=3.0, avg_trade=3.0)
    r2 = iv.evaluate_cp2(lm2, ref)
    assert r2["hard_gate_failures"] == []
    assert r2["failing_count"] == 2
    assert r2["verdict"] == "OBSERVAR"
    assert r2["metrics_evaluation"]["expectancy"]["status"] == "failing"
    assert r2["metrics_evaluation"]["avg_trade"]["status"] == "failing"

    # win_rate + expectancy -> 1 + 2 = 3 failing -> ELIMINAR
    lm3 = make_live_metrics(total_trades=30, winning_trades=15, win_rate=44.0,
                             expectancy=3.0, avg_trade=3.0)
    r3 = iv.evaluate_cp2(lm3, ref)
    assert r3["hard_gate_failures"] == []
    assert r3["failing_count"] == 3
    assert r3["verdict"] == "ELIMINAR"


def test_cp2_hard_gate_failure_short_circuits_to_eliminar_without_metrics_evaluation():
    ref = make_reference()
    lm = make_live_metrics(total_trades=30, winning_trades=17, max_dd_pct=99.0)
    r = iv.evaluate_cp2(lm, ref)
    assert r["hard_gate_failures"] != []
    assert r["verdict"] == "ELIMINAR"
    assert r["metrics_evaluation"] == {}
    assert r["failing_count"] is None


# ── §7 / §3.19: CP3 _score_metric (interpolacion continua) ─────────────────

def test_score_metric_higher_is_better_branches():
    # live >= bt -> 100
    assert iv._score_metric(12.0, 5.0, 8.0, 10.0, higher_is_better=True) == 100.0
    # bt > live >= mc50 -> 65 + 35*(live-mc50)/(bt-mc50)
    assert iv._score_metric(9.0, 5.0, 8.0, 10.0, higher_is_better=True) == pytest.approx(65 + 35 * 1 / 2)
    # mc50 > live >= mc95 -> 25 + 40*(live-mc95)/(mc50-mc95)
    assert iv._score_metric(6.0, 5.0, 8.0, 10.0, higher_is_better=True) == pytest.approx(25 + 40 * 1 / 3)
    # live < mc95 -> max(0, 25*live/mc95)
    assert iv._score_metric(2.0, 5.0, 8.0, 10.0, higher_is_better=True) == pytest.approx(25 * 2 / 5)
    # live negativo bajo mc95 -> clamp a 0.0, no negativo
    assert iv._score_metric(-100.0, 5.0, 8.0, 10.0, higher_is_better=True) == 0.0


def test_score_metric_lower_is_better_branches():
    assert iv._score_metric(8.0, 20.0, 15.0, 10.0, higher_is_better=False) == 100.0
    assert iv._score_metric(12.0, 20.0, 15.0, 10.0, higher_is_better=False) == pytest.approx(65 + 35 * 3 / 5)
    assert iv._score_metric(18.0, 20.0, 15.0, 10.0, higher_is_better=False) == pytest.approx(25 + 40 * 2 / 5)
    assert iv._score_metric(40.0, 20.0, 15.0, 10.0, higher_is_better=False) == pytest.approx(25 * 20 / 40)


def test_score_metric_max_0_001_guard_prevents_zero_division():
    """
    Guardia max(mc95_value, 0.001) en la rama catch-all (live < mc95,
    incubation_validator.py:805): con mc95_value==0.0 exactamente, una
    division directa por mc95_value crashearia con ZeroDivisionError. La
    guardia sustituye 0.001 como piso, y el resultado exterior sigue
    clampeado a 0.0 via max(0.0, ...) porque live_value es negativo aqui
    -- se pinnea el hecho de que NO CRASHEA, no un valor numerico
    interesante.
    """
    val = iv._score_metric(-1.0, 0.0, 8.0, 10.0, higher_is_better=True)
    assert val == 0.0


def test_score_metric_bt_equals_mc50_branch_is_unreachable_for_higher_is_better():
    """
    CARACTERIZACION (no defecto): la rama "mc50<=live<bt" solo se alcanza
    cuando bt>mc50 estrictamente (de lo contrario "live>=bt" ya devolvio
    100.0 antes de llegar al elif). Con bt==mc50 exactos, cualquier live
    que cumpla "live>=mc50" tambien cumple "live>=bt" y sale por la PRIMERA
    rama -- la guardia max(bt-mc50, 0.001) de esa rama especifica es, por
    construccion de los `if/elif` en cascada, codigo muerto para
    higher_is_better. Se fija aqui el comportamiento real: bt==mc50 con
    live>=bt siempre da 100.0, nunca pasa por el denominador (bt-mc50).
    """
    val = iv._score_metric(10.0, 5.0, 10.0, 10.0, higher_is_better=True)
    assert val == 100.0


# ── §3.18: CP3 verdict 65/45 + below_mc95 + escalation CP2->CP3 ────────────

def test_cp3_verdict_score_above_65_but_below_mc95_blocks_aprobar():
    """
    score alto (94.38, >=65) pero win_rate por debajo de mc95 -> below_mc95
    no vacio -> APROBAR bloqueado, cae a OBSERVAR (incubation_validator.py:
    830, gate que decision-logic.md omite en su resumen).
    """
    ref = make_reference()
    lm = make_live_metrics(total_trades=80, winning_trades=44, win_rate=30.0)
    r = iv.evaluate_cp3(lm, ref)
    assert r["score"] == 94.38
    assert r["verdict"] == "OBSERVAR"
    assert "win_rate" in [
        k for k, v in r["metrics_scores"].items()
        if k == "win_rate" and v["live"] < v["mc95"]
    ]


def test_cp3_escalation_from_cp2_observar_forces_eliminar():
    """
    Anti-limbo: si CP2 dio OBSERVAR y CP3 vuelve a dar OBSERVAR por su
    cuenta, escala a ELIMINAR (incubation_validator.py:837-840).
    """
    ref = make_reference()
    lm = make_live_metrics(
        total_trades=80, winning_trades=40, win_rate=50.0, profit_factor=1.2,
        expectancy=7.0, avg_trade=7.0, payout_ratio=1.05, ret_dd=1.8,
        max_dd_pct=17.0, max_consec_losses=7, stagnation_days=50.0,
    )
    r_alone = iv.evaluate_cp3(lm, ref)
    assert r_alone["score"] == 62.46
    assert r_alone["verdict"] == "OBSERVAR"
    assert r_alone["escalation_from_cp2"] is False

    r_escalated = iv.evaluate_cp3(lm, ref, previous_cp2_result={"verdict": "OBSERVAR"})
    assert r_escalated["score"] == 62.46
    assert r_escalated["verdict"] == "ELIMINAR"
    assert r_escalated["escalation_from_cp2"] is True


def test_cp3_rounded_vs_raw_score_split_at_65_is_closed():
    """
    REGRESIÓN DE CONSISTENCIA (fix 4A). Antes del fix esto era un DEFECT-PIN:
    CP3 decidía el veredicto sobre el `final_score` SIN redondear mientras
    `result["score"]` era la versión YA redondeada a 2dp, así que un crudo
    infinitesimalmente por debajo de 65.0 daba OBSERVAR pero se mostraba 65.0
    (frontera de APROBAR). A diferencia del validador (cuya grilla discreta es
    SIEMPRE consistente, ver test_char_validator.py), CP3 usa interpolación
    CONTINUA (`_score_metric`), así que el split SÍ era alcanzable: este
    live_metrics se construyó por bisección contra la función real para caer
    justo en ese punto (T=0.804...).

    Tras el fix, `evaluate_cp3` canoniza sobre el valor publicado: redondea una
    vez y decide con ese mismo score. El crudo que redondea a 65.0 ahora decide
    APROBAR, así que el número mostrado y el veredicto salen del MISMO valor y
    el split queda cerrado (incubation_validator.py, `published_score`).
    """
    mc95v = dict(win_rate=45.0, profit_factor=1.1, expectancy=5.0, avg_trade=5.0,
                 payout_ratio=1.0, ret_dd_ratio=1.5, max_dd_pct=20.0,
                 max_consec_losses=8, stagnation_days=60.0)
    mc50v = dict(win_rate=50.0, profit_factor=1.3, expectancy=8.0, avg_trade=8.0,
                 payout_ratio=1.1, ret_dd_ratio=2.0, max_dd_pct=15.0,
                 max_consec_losses=6, stagnation_days=45.0)
    btv = dict(win_rate=55.0, profit_factor=1.5, expectancy=10.0, avg_trade=10.0,
               payout_ratio=1.2, ret_dd_ratio=2.5, max_dd_pct=10.0,
               max_consec_losses=5, stagnation_days=30.0)

    reference = make_reference(
        backtest=dict(
            win_rate=btv["win_rate"], total_trades=120, bt_period="2025.01.01 - 2025.12.31",
            profit_factor=btv["profit_factor"], expectancy=btv["expectancy"],
            payout_ratio=btv["payout_ratio"], ret_dd_ratio=btv["ret_dd_ratio"],
            max_dd_pct=btv["max_dd_pct"], max_consec_losses=btv["max_consec_losses"],
            stagnation_days=btv["stagnation_days"], avg_trade=btv["avg_trade"],
        ),
        mc_manipulation={"confidence_95": dict(mc95v), "confidence_50": dict(mc50v)},
        mc_retest={"confidence_95": dict(mc95v), "confidence_50": dict(mc50v)},
    )

    def interp(key, t):
        a, b = mc95v[key], mc50v[key]
        return a + (b - a) * t

    # T obtenido por biseccion binaria contra evaluate_cp3 real: el punto exacto
    # donde el score crudo cae infinitesimalmente por debajo de 65.0 y redondea
    # a 65.0. Antes del fix el veredicto salía OBSERVAR; ahora, canonizando sobre
    # el valor publicado, decide APROBAR con el MISMO 65.0 que se muestra.
    T = 0.8048214285714284
    live = dict(
        total_trades=80, winning_trades=45,
        win_rate=interp("win_rate", T),
        profit_factor=interp("profit_factor", T),
        expectancy=interp("expectancy", T),
        payout_ratio=interp("payout_ratio", T),
        ret_dd=interp("ret_dd_ratio", T),
        max_dd_pct=interp("max_dd_pct", T),
        max_consec_losses=8,  # fijo en mc95 (parseado como int, sin fraccion)
        stagnation_days=interp("stagnation_days", T),
        avg_trade=interp("expectancy", T),
        trades=[],
    )
    r = iv.evaluate_cp3(live, reference)
    assert r["score"] == 65.0
    assert r["verdict"] == "APROBAR"  # coherente con el 65.0 mostrado (fix 4A)
    assert r["hard_gate_failures"] == []


def test_cp3_verdict_clear_eliminar_below_45():
    ref = make_reference()
    lm = make_live_metrics(
        total_trades=50, winning_trades=22, win_rate=45.0, profit_factor=1.1,
        expectancy=5.0, avg_trade=5.0, payout_ratio=1.0, ret_dd=1.5,
        max_dd_pct=20.0, max_consec_losses=8, stagnation_days=60.0,
    )
    r = iv.evaluate_cp3(lm, ref)
    assert r["score"] == 39.75
    assert r["verdict"] == "ELIMINAR"


def test_cp3_verdict_clear_aprobar_above_65_no_below_mc95():
    ref = make_reference()
    lm = make_live_metrics(total_trades=50, winning_trades=28)  # sano por defecto
    r = iv.evaluate_cp3(lm, ref)
    assert r["score"] == 96.0
    assert r["verdict"] == "APROBAR"


# ── §3.19: CP3 bandas de coherencia y de muestra ────────────────────────────

@pytest.mark.parametrize("total_trades,coherence_score", [
    (10, 10.0),   # ratio ~0.156 -> fuera de [0.25,3.0]
    (26, 50.0),   # ratio ~0.404 -> [0.25,0.5)
    (39, 100.0),  # ratio ~0.607 -> [0.5,2.0]
    (161, 50.0),  # ratio ~2.504 -> (2.0,3.0]
    (258, 10.0),  # ratio ~4.013 -> fuera
])
def test_cp3_coherence_bands(total_trades, coherence_score):
    ref = make_reference()
    lm = make_live_metrics(total_trades=total_trades, winning_trades=int(total_trades * 0.56))
    r = iv.evaluate_cp3(lm, ref)
    assert r["category_scores"]["coherence"]["score"] == coherence_score


@pytest.mark.parametrize("total_trades,sample_score", [
    (39, 40.0), (40, 60.0), (59, 60.0), (60, 80.0), (79, 80.0), (80, 100.0),
])
def test_cp3_sample_bands_reachable_only_via_direct_evaluate_cp3_call(total_trades, sample_score):
    """
    sample_score==40.0 es INALCANZABLE via evaluate_incubation (CP3 requiere
    total_trades>=40 para siquiera despachar a CP3) pero SI es alcanzable
    llamando evaluate_cp3 directamente -- se pinnea aqui como caracterizacion
    de la funcion interna, no del flujo publico completo.
    """
    ref = make_reference()
    lm = make_live_metrics(total_trades=total_trades, winning_trades=int(total_trades * 0.56))
    r = iv.evaluate_cp3(lm, ref)
    assert r["category_scores"]["sample"]["score"] == sample_score
    assert "details" not in r["category_scores"]["sample"]  # forma asimetrica (§4.3)


def test_cp3_hard_gate_failure_short_circuits_before_scoring():
    ref = make_reference()
    lm = make_live_metrics(total_trades=50, winning_trades=28, max_dd_pct=99.0)
    r = iv.evaluate_cp3(lm, ref)
    assert r["hard_gate_failures"] != []
    assert r["verdict"] == "ELIMINAR"
    assert r["score"] is None
    assert r["category_scores"] == {}
    assert r["metrics_scores"] == {}


# ── §3.20: PRE_CP1 deadline (evaluate_incubation) ───────────────────────────

def test_pre_cp1_pending_when_within_deadline():
    ref = make_reference(date_added="2026-07-10")  # 6 dias antes del reloj congelado
    lm = make_live_metrics(total_trades=2, winning_trades=1, trades=[])
    r = iv.evaluate_incubation("EA", lm, ref)
    assert r["current_checkpoint"] == "PRE_CP1"
    assert r["verdict"] == "PENDING"
    assert r["days_incubating"] == 6
    assert r["details"]["freq_deadline"] is False


def test_pre_cp1_eliminar_when_deadline_exceeded():
    ref = make_reference(date_added="2026-01-02")  # 195 dias antes del reloj congelado
    lm = make_live_metrics(total_trades=2, winning_trades=1, trades=[])
    r = iv.evaluate_incubation("EA", lm, ref)
    assert r["current_checkpoint"] == "PRE_CP1"
    assert r["verdict"] == "ELIMINAR"
    assert r["days_incubating"] == 195
    assert r["hard_gate_failures"] == ["freq_deadline"]
    assert r["details"]["freq_deadline"] is True


def test_pre_cp1_sin_datos_when_backtest_reference_missing():
    lm = make_live_metrics(total_trades=2, winning_trades=1, trades=[])
    r = iv.evaluate_incubation("EA", lm, {"date_added": "2026-01-02"})
    assert r["verdict"] == "SIN DATOS"
    assert r["sin_datos"] is True
    assert r["mc_source"] == {}


def test_pre_cp1_eliminar_and_pending_shapes_omit_mc_source_key_entirely():
    """
    DEFECT-PIN: la forma SIN DATOS de PRE_CP1 (:1214-1230) SI setea
    "mc_source": {}, pero las formas ELIMINAR (:1241-1259) y PENDING
    (:1261-1279) de PRE_CP1 no incluyen la clave "mc_source" EN ABSOLUTO --
    ni siquiera como {} -- porque son literales de diccionario separados
    que nunca pasan por el `result.get("mc_source", {})` del retorno
    unificado (:1301). Un consumidor que hace r["mc_source"] sin .get()
    crashea sobre estas dos formas especificamente. Pinneado porque es el
    comportamiento actual, NO porque sea correcto.
    """
    ref_eliminar = make_reference(date_added="2026-01-02")
    lm = make_live_metrics(total_trades=2, winning_trades=1, trades=[])
    r_eliminar = iv.evaluate_incubation("EA", lm, ref_eliminar)
    assert r_eliminar["verdict"] == "ELIMINAR"
    assert "mc_source" not in r_eliminar

    ref_pending = make_reference(date_added="2026-07-10")
    r_pending = iv.evaluate_incubation("EA", lm, ref_pending)
    assert r_pending["verdict"] == "PENDING"
    assert "mc_source" not in r_pending

    # contraste: la forma SIN DATOS de PRE_CP1 SI la incluye
    ref_sd = {"date_added": "2026-01-02"}
    r_sd = iv.evaluate_incubation("EA", lm, ref_sd)
    assert "mc_source" in r_sd
    assert r_sd["mc_source"] == {}


# ── evaluate_incubation -- forma unificada para CP1/CP2/CP3 ────────────────

def test_evaluate_incubation_unified_shape_for_cp1():
    ref = make_reference()
    lm = make_live_metrics(total_trades=10, winning_trades=6, trades=[])
    r = iv.evaluate_incubation("EA", lm, ref)
    assert set(r.keys()) == {
        "ea_name", "total_trades", "days_incubating", "current_checkpoint",
        "verdict", "score", "sin_datos", "missing", "details",
        "hard_gate_failures", "mc_source", "timestamp",
    }
    assert r["current_checkpoint"] == "CP1"
    assert r["verdict"] == "CONTINUAR"
    assert r["score"] is None
    assert r["details"]["checkpoint"] == "CP1"


# ── §4 (dispatch de checkpoints): evaluate_incubation SI enruta a CP2/CP3 ──
#
# El resto de este archivo y de test_prop_incubation.py llama a evaluate_cp2/
# evaluate_cp3 DIRECTAMENTE con total_trades en {25, 30, 40, 50, 80, ...}, o
# a evaluate_incubation con total_trades en {2, 10} (que solo alcanza
# PRE_CP1/CP1, incubation_validator.py:1281-1286). Ningún test anterior
# probaba que evaluate_incubation('EA', ..., total_trades=25/50, ...)
# REALMENTE despacha al checkpoint correcto y que la porción CP2/CP3 de su
# salida coincide con la llamada directa -- el cableado del dispatcher
# (:1283 elif checkpoint=='CP2', :1286 else evaluate_cp3) quedaba sin cubrir.

def test_evaluate_incubation_dispatches_to_cp2_and_matches_direct_call():
    """total_trades=25 esta en el rango CP2 (20<=n<40, incubation_validator.py:
    306-313). evaluate_incubation debe reportar current_checkpoint=='CP2' Y su
    'details' debe ser EXACTAMENTE el dict que devuelve una llamada directa a
    evaluate_cp2 con los mismos argumentos -- no solo un shape compatible."""
    ref = make_reference()
    lm = make_live_metrics(total_trades=25, winning_trades=14)

    r = iv.evaluate_incubation("EA", copy.deepcopy(lm), copy.deepcopy(ref))
    direct = iv.evaluate_cp2(copy.deepcopy(lm), copy.deepcopy(ref))

    assert r["current_checkpoint"] == "CP2"
    assert r["verdict"] == direct["verdict"]
    assert r["score"] == direct["score"]
    # evaluate_cp2's return dict never has a "timestamp" key of its own (see
    # evaluate_cp2 at incubation_validator.py:766-781), so "details" (the
    # wrapped result) equals the direct call verbatim -- no field needs
    # excluding here.
    assert r["details"] == direct


def test_evaluate_incubation_dispatches_to_cp3_and_matches_direct_call():
    """total_trades=50 esta en el rango CP3 (n>=40). evaluate_incubation debe
    reportar current_checkpoint=='CP3' Y su 'details' debe ser EXACTAMENTE el
    dict que devuelve una llamada directa a evaluate_cp3 -- mismo contrato
    que el test de CP2 de arriba, para la otra rama del dispatcher
    (incubation_validator.py:1286)."""
    ref = make_reference()
    lm = make_live_metrics(total_trades=50, winning_trades=28)

    r = iv.evaluate_incubation("EA", copy.deepcopy(lm), copy.deepcopy(ref))
    direct = iv.evaluate_cp3(copy.deepcopy(lm), copy.deepcopy(ref))

    assert r["current_checkpoint"] == "CP3"
    assert r["verdict"] == direct["verdict"]
    assert r["score"] == direct["score"]
    # Same rationale as the CP2 test above: evaluate_cp3's return dict has no
    # "timestamp" key of its own (incubation_validator.py:1167-1180), so
    # "details" equals the direct call verbatim.
    assert r["details"] == direct
