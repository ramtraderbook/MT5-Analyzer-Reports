"""
test_char_validator.py - Caracterizacion de validator.py (capa 1, harness P-A).

Fija el comportamiento ACTUAL de calculate_validator_score() y
get_all_validator_results() -- lo que HAY, no lo que DEBERIA haber.
Los defectos conocidos se marcan con DEFECT-PIN. Todas las entradas son
hardcodeadas via los helpers de conftest.py (make_bt/make_live/make_mc/
make_spp/make_config); nada se lee de disco.

Todos los valores numericos de este archivo fueron verificados llamando a
la funcion real (no derivados solo a mano) antes de hardcodear el
assert -- ver el reporte de la tarea para el detalle de cada busqueda.
"""

import pytest

import validator as v
from conftest import make_bt, make_live, make_mc, make_spp, make_config


# ── §1.1: conjunto exacto de claves de las 3 formas de salida ──────────────
# _nd_result() (SIN DATOS / ELIMINAR-early) y el camino "full scored" (:689)
# devuelven el MISMO conjunto de 44 claves -- solo cambia que campos llevan
# valores reales vs None/"N/D". Verificado empiricamente contra el dict real.
RESULT_KEYS = {
    "veredicto", "accion", "sin_datos", "score", "desv_flag", "missing",
    "signif", "trades_live", "weeks_live",
    "wr_delta", "wr_live", "wr_bt", "wr_estado",
    "pf_live", "pf_bt", "pf_estado",
    "payout_var", "payout_live", "payout_bt", "payout_estado",
    "dd_live", "dd_limit", "dd_method", "dd_estado",
    "consec_ratio", "consec_estado",
    "bars_var", "avg_bars_live", "avg_bars_bt", "bars_estado",
    "freq_pct", "freq_estado",
    "edge_erosion", "expect_live", "spp_expect_median", "edge_estado",
    "stagn_live", "stagn_bt", "stagn_label", "stagn_estado",
    "s_riesgo", "s_edge", "s_caracter", "s_desv", "detcount",
    "live_vs_bt_profit_ratio", "live_vs_bt_profit_status",
}


def test_sin_datos_shape_when_too_few_trades_and_still_early():
    """tl<5 y semanas<8 -> SIN DATOS temprano (validator.py:191-199)."""
    r = v.calculate_validator_score(
        make_bt(), make_mc(12.0), make_mc(14.0), make_spp(),
        make_live(total_trades=3, weeks_operating=1.0),
    )
    assert r["veredicto"] == "SIN DATOS"
    assert r["sin_datos"] is True
    assert r["score"] is None
    assert r["desv_flag"] == "-"
    assert r["trades_live"] == 3
    assert set(r.keys()) == RESULT_KEYS


def test_eliminar_shape_when_too_few_trades_and_weeks_exhausted():
    """tl<5 pero semanas>=8 -> ELIMINAR temprano (validator.py:200-206)."""
    r = v.calculate_validator_score(
        make_bt(), make_mc(12.0), make_mc(14.0), make_spp(),
        make_live(total_trades=3, weeks_operating=8.0),
    )
    assert r["veredicto"] == "ELIMINAR"
    assert r["sin_datos"] is True
    assert r["score"] is None
    assert set(r.keys()) == RESULT_KEYS


def test_full_scored_shape_key_set_and_types():
    r = v.calculate_validator_score(
        make_bt(), make_mc(12.0), make_mc(14.0), make_spp(), make_live()
    )
    assert r["sin_datos"] is False
    assert r["missing"] == []
    assert set(r.keys()) == RESULT_KEYS
    assert isinstance(r["score"], float)
    # todos los *_estado escalonados deben ser uno de estos 3 -- "N/D" es
    # inalcanzable en esta forma para los 9 estados scoreados (gate #2,
    # validator.py:500-555). live_vs_bt_profit_status esta explicitamente exento.
    for key in ("wr_estado", "pf_estado", "payout_estado", "dd_estado",
                "consec_estado", "bars_estado", "freq_estado", "edge_estado",
                "stagn_estado"):
        assert r[key] in ("OK", "ALERTA", "FUERA")


# ── §3.2: guardia de datos minimos (validator.py:191-206) ──────────────────

def test_min_data_gate_tl_5_evaluates_normally():
    """tl==5 (== min_trades) evalua normalmente, sin importar las semanas."""
    r = v.calculate_validator_score(
        make_bt(worst_dd_1m=8.0), make_mc(12.0), make_mc(14.0), make_spp(),
        make_live(total_trades=5, weeks_operating=0.1, max_dd_pct=5.0),
    )
    assert r["sin_datos"] is False
    assert r["veredicto"] in ("CONTINUAR", "MONITOREAR", "ELIMINAR")


def test_min_data_gate_tl_4_weeks_7_9_is_sin_datos():
    r = v.calculate_validator_score(
        make_bt(), make_mc(12.0), make_mc(14.0), make_spp(),
        make_live(total_trades=4, weeks_operating=7.9),
    )
    assert r["veredicto"] == "SIN DATOS"


def test_min_data_gate_tl_4_weeks_8_0_is_eliminar():
    """El borde weeks_live>=max_wait_weeks(8) cae al lado ELIMINAR."""
    r = v.calculate_validator_score(
        make_bt(), make_mc(12.0), make_mc(14.0), make_spp(),
        make_live(total_trades=4, weeks_operating=8.0),
    )
    assert r["veredicto"] == "ELIMINAR"


# ── §3.3: bandas de significancia (validator.py:135-142) ───────────────────

@pytest.mark.parametrize("tl,expected", [
    (100, "Alta"),
    (99, "Media"),
    (50, "Media"),
    (49, "Baja"),
    (30, "Baja"),
    (29, "Muy baja"),
])
def test_significance_bands(tl, expected):
    r = v.calculate_validator_score(
        make_bt(worst_dd_1m=8.0), make_mc(12.0), make_mc(14.0), make_spp(),
        make_live(total_trades=tl, max_dd_pct=5.0),
    )
    assert r["signif"] == expected
    assert r["trades_live"] == tl


# ── §3.4: Win Rate por banda de tl, con operador <= exacto ─────────────────

@pytest.mark.parametrize("delta,estado", [(5.0, "OK"), (10.0, "ALERTA"), (10.1, "FUERA")])
def test_win_rate_band_tl_ge_100(delta, estado):
    """tl>=100: OK<=5, ALERTA<=10, FUERA>10 (validator.py:271-272)."""
    r = v.calculate_validator_score(
        make_bt(win_rate=50.0), make_mc(12.0), make_mc(14.0), make_spp(),
        make_live(total_trades=100, win_rate=50.0 + delta),
    )
    assert r["wr_estado"] == estado
    assert r["wr_delta"] == round(delta, 2)


@pytest.mark.parametrize("delta,estado", [(7.0, "OK"), (12.0, "ALERTA"), (12.1, "FUERA")])
def test_win_rate_band_tl_50_to_99(delta, estado):
    """tl in [50,100): OK<=7, ALERTA<=12 (validator.py:269-270)."""
    r = v.calculate_validator_score(
        make_bt(win_rate=50.0), make_mc(12.0), make_mc(14.0), make_spp(),
        make_live(total_trades=60, win_rate=50.0 + delta),
    )
    assert r["wr_estado"] == estado


@pytest.mark.parametrize("delta,estado", [(10.0, "OK"), (15.0, "ALERTA"), (15.1, "FUERA")])
def test_win_rate_band_tl_30_to_49(delta, estado):
    """tl in [30,50): OK<=10, ALERTA<=15 (validator.py:267-268)."""
    r = v.calculate_validator_score(
        make_bt(win_rate=50.0), make_mc(12.0), make_mc(14.0), make_spp(),
        make_live(total_trades=35, win_rate=50.0 + delta),
    )
    assert r["wr_estado"] == estado


@pytest.mark.parametrize("delta,estado", [(15.0, "OK"), (20.0, "ALERTA"), (20.1, "FUERA")])
def test_win_rate_band_tl_lt_30(delta, estado):
    """tl<30: OK<=15, ALERTA<=20 (validator.py:265-266)."""
    r = v.calculate_validator_score(
        make_bt(win_rate=50.0), make_mc(12.0), make_mc(14.0), make_spp(),
        make_live(total_trades=10, win_rate=50.0 + delta),
    )
    assert r["wr_estado"] == estado


# ── §3.5: Profit Factor por banda de tl ─────────────────────────────────────

@pytest.mark.parametrize("pf_live,estado", [(0.8, "OK"), (0.5, "ALERTA"), (0.49, "FUERA")])
def test_profit_factor_band_tl_lt_30(pf_live, estado):
    r = v.calculate_validator_score(
        make_bt(), make_mc(12.0), make_mc(14.0), make_spp(),
        make_live(total_trades=10, profit_factor=pf_live),
    )
    assert r["pf_estado"] == estado


@pytest.mark.parametrize("pf_live,estado", [(1.0, "OK"), (0.8, "ALERTA"), (0.79, "FUERA")])
def test_profit_factor_band_tl_30_to_49(pf_live, estado):
    r = v.calculate_validator_score(
        make_bt(), make_mc(12.0), make_mc(14.0), make_spp(),
        make_live(total_trades=35, profit_factor=pf_live),
    )
    assert r["pf_estado"] == estado


@pytest.mark.parametrize("pf_live,estado", [(1.5, "OK"), (1.0, "ALERTA"), (0.99, "FUERA")])
def test_profit_factor_band_tl_ge_50_uses_bt_ref(pf_live, estado):
    """tl>=50: la referencia OK es bt_pf (no 1.0 fijo) (validator.py:291-295)."""
    r = v.calculate_validator_score(
        make_bt(profit_factor=1.5), make_mc(12.0), make_mc(14.0), make_spp(),
        make_live(total_trades=60, profit_factor=pf_live),
    )
    assert r["pf_estado"] == estado


# ── §3.6: Payout, incl. el atajo >=1e9 -> OK ────────────────────────────────

def test_payout_infinity_sentinel_is_ok_short_circuit():
    """payout_ratio="∞" -> _safe_float=1e9 -> atajo OK, payout_var=None."""
    r = v.calculate_validator_score(
        make_bt(worst_dd_1m=8.0), make_mc(12.0), make_mc(14.0), make_spp(),
        make_live(payout_ratio="∞", max_dd_pct=5.0),
    )
    assert r["payout_estado"] == "OK"
    assert r["payout_live"] == 1e9
    assert r["payout_var"] is None


@pytest.mark.parametrize("abs_pv,estado", [(40.0, "OK"), (59.9, "ALERTA"), (60.1, "FUERA")])
def test_payout_band_tl_lt_30(abs_pv, estado):
    r = v.calculate_validator_score(
        make_bt(payout_ratio=1.0), make_mc(12.0), make_mc(14.0), make_spp(),
        make_live(total_trades=10, payout_ratio=1.0 * (1 + abs_pv / 100)),
    )
    assert r["payout_estado"] == estado


@pytest.mark.parametrize("abs_pv,estado", [(29.9, "OK"), (50.0, "ALERTA"), (50.1, "FUERA")])
def test_payout_band_tl_30_to_49(abs_pv, estado):
    r = v.calculate_validator_score(
        make_bt(payout_ratio=1.0), make_mc(12.0), make_mc(14.0), make_spp(),
        make_live(total_trades=35, payout_ratio=1.0 * (1 + abs_pv / 100)),
    )
    assert r["payout_estado"] == estado


@pytest.mark.parametrize("abs_pv,estado", [(25.0, "OK"), (40.0, "ALERTA"), (40.1, "FUERA")])
def test_payout_band_tl_ge_50(abs_pv, estado):
    r = v.calculate_validator_score(
        make_bt(payout_ratio=1.0), make_mc(12.0), make_mc(14.0), make_spp(),
        make_live(total_trades=60, payout_ratio=1.0 * (1 + abs_pv / 100)),
    )
    assert r["payout_estado"] == estado


# ── §3.7: DD escalado (reloj de trades) + fallback MC ───────────────────────

@pytest.mark.parametrize("max_dd_live,estado", [(16.0, "OK"), (24.0, "ALERTA"), (24.01, "FUERA")])
def test_dd_escalado_trade_clock_boundaries(max_dd_live, estado):
    """bt_freq_mes=120/12=10, trades_live=40 -> dd_limit=8*sqrt(40/10)=16.0 exacto."""
    r = v.calculate_validator_score(
        make_bt(worst_dd_1m=8.0, trades_total=120, months=12.0),
        make_mc(12.0), make_mc(14.0), make_spp(),
        make_live(total_trades=40, max_dd_pct=max_dd_live),
    )
    assert r["dd_estado"] == estado
    assert r["dd_limit"] == 16.0
    assert r["dd_method"].startswith("sqrt(")


def test_dd_mc_fallback_used_when_no_bt_trade_clock_data():
    """bt_worst_dd_1m<=0 -> cae al fallback MC min(retest,trades)."""
    r = v.calculate_validator_score(
        make_bt(worst_dd_1m=0.0), make_mc(12.0), make_mc(18.0), make_spp(),
        make_live(max_dd_pct=12.0),
    )
    assert r["dd_method"] == "MC min(Retest,Trades) 95% (fallback)"
    assert r["dd_limit"] == 12.0  # min(12,18)


def test_dd_mc_fallback_empty_alerta_quirk_is_pinned():
    # DEFECT-PIN: cuando mc_retest.max_dd == mc_trades.max_dd, max()==min() y
    # la zona ALERTA queda vacia -- el gate colapsa a solo OK/FUERA en vez de
    # OK/ALERTA/FUERA. Pinneado porque es el comportamiento actual
    # (validator.py:371-379, known-issues.md §4), NO porque sea correcto.
    r_ok = v.calculate_validator_score(
        make_bt(worst_dd_1m=0.0), make_mc(15.0), make_mc(15.0), make_spp(),
        make_live(max_dd_pct=15.0),
    )
    assert r_ok["dd_estado"] == "OK"
    r_fuera = v.calculate_validator_score(
        make_bt(worst_dd_1m=0.0), make_mc(15.0), make_mc(15.0), make_spp(),
        make_live(max_dd_pct=15.01),
    )
    # DEFECT-PIN: salta directo de OK a FUERA -- nunca hay ALERTA posible
    # cuando ambos MC coinciden.
    assert r_fuera["dd_estado"] == "FUERA"


# ── §3.8: Consec losses ──────────────────────────────────────────────────

@pytest.mark.parametrize("cl_live,estado", [(5, "OK"), (7, "ALERTA"), (8, "FUERA")])
def test_consec_losses_bands(cl_live, estado):
    """cl_bt=5 -> OK<=5, ALERTA<=7.5(trunc a 7 sigue ALERTA), FUERA>7.5."""
    r = v.calculate_validator_score(
        make_bt(max_consec_losses=5), make_mc(12.0), make_mc(14.0), make_spp(),
        make_live(max_consec_losses=cl_live),
    )
    assert r["consec_estado"] == estado
    assert r["consec_ratio"] == f"{cl_live}/5"


# ── §3.9: Avg bars/trade ────────────────────────────────────────────────

@pytest.mark.parametrize("abs_bv,estado", [(50.0, "OK"), (70.0, "ALERTA"), (70.1, "FUERA")])
def test_avg_bars_band_tl_lt_30(abs_bv, estado):
    r = v.calculate_validator_score(
        make_bt(avg_bars=20.0), make_mc(12.0), make_mc(14.0), make_spp(),
        make_live(total_trades=10, avg_bars_live=20.0 * (1 + abs_bv / 100)),
    )
    assert r["bars_estado"] == estado


@pytest.mark.parametrize("abs_bv,estado", [(30.0, "OK"), (50.0, "ALERTA"), (50.1, "FUERA")])
def test_avg_bars_band_tl_ge_30(abs_bv, estado):
    r = v.calculate_validator_score(
        make_bt(avg_bars=20.0), make_mc(12.0), make_mc(14.0), make_spp(),
        make_live(total_trades=35, avg_bars_live=20.0 * (1 + abs_bv / 100)),
    )
    assert r["bars_estado"] == estado


# ── §3.10: Frecuencia -- DOS COLAS (validator.py:432-447) ──────────────────
# Bordes obtenidos por busqueda binaria contra la funcion real: weeks_live
# controla freq_pct de forma continua para bt_trades=120/bt_months=12.

def test_frequency_ok_alerta_boundary_deviation_30():
    r_ok = v.calculate_validator_score(
        make_bt(), make_mc(12.0), make_mc(14.0), make_spp(),
        make_live(weeks_operating=33.307692307692314),  # freq_pct == 130.0, dev == 30
    )
    assert r_ok["freq_pct"] == 130.0
    assert r_ok["freq_estado"] == "OK"
    r_alerta = v.calculate_validator_score(
        make_bt(), make_mc(12.0), make_mc(14.0), make_spp(),
        make_live(weeks_operating=33.30769230769231),  # infinitesimo por debajo
    )
    assert r_alerta["freq_estado"] == "ALERTA"


def test_frequency_alerta_fuera_boundary_deviation_50():
    r_alerta = v.calculate_validator_score(
        make_bt(), make_mc(12.0), make_mc(14.0), make_spp(),
        make_live(weeks_operating=28.866666666666667),  # freq_pct == 150.0, dev == 50
    )
    assert r_alerta["freq_pct"] == 150.0
    assert r_alerta["freq_estado"] == "ALERTA"
    r_fuera = v.calculate_validator_score(
        make_bt(), make_mc(12.0), make_mc(14.0), make_spp(),
        make_live(weeks_operating=28.866666666666664),
    )
    assert r_fuera["freq_estado"] == "FUERA"


def test_frequency_overtrading_far_above_bt_pace_is_fuera_not_ok():
    """
    DEFECT-PIN referencia historica: la doc decision-logic.md (seccion
    "Frecuencia", Modulo 1) describe esto como de UNA cola (OK si
    freq_pct>=70). El
    codigo real es de DOS colas: sobre-operar muy por encima del ritmo BT
    tambien es FUERA. Esto ya esta documentado como cambio intencional en
    el propio codigo (validator.py:436-443) -- se pinnea aqui como
    caracterizacion del comportamiento REAL, que diverge de
    decision-logic.md.
    """
    r = v.calculate_validator_score(
        make_bt(), make_mc(12.0), make_mc(14.0), make_spp(),
        make_live(total_trades=100, weeks_operating=10.0),  # muy por encima del ritmo BT
    )
    assert r["freq_pct"] > 150.0
    assert r["freq_estado"] == "FUERA"


# ── §3.11: Edge erosion ────────────────────────────────────────────────────

@pytest.mark.parametrize("erosion_pct,estado", [(-30.0, "OK"), (-60.0, "ALERTA"), (-60.1, "FUERA")])
def test_edge_erosion_bands(erosion_pct, estado):
    r = v.calculate_validator_score(
        make_bt(), make_mc(12.0), make_mc(14.0), make_spp(expectancy_median=10.0),
        make_live(expectancy=10.0 * (1 + erosion_pct / 100)),
    )
    assert r["edge_estado"] == estado


# ── §3.12: Stagnation, con bt_stagnation presente vs ausente ────────────────

def test_stagnation_bands_with_bt_reference():
    """bt_stagnation=30: Normal<=9, Elevada<=18, Alta>18."""
    r_normal = v.calculate_validator_score(
        make_bt(stagnation_days=30.0), make_mc(12.0), make_mc(14.0), make_spp(),
        make_live(stagnation_days=9.0),
    )
    assert r_normal["stagn_label"] == "Normal"
    assert r_normal["stagn_estado"] == "OK"
    r_elevada = v.calculate_validator_score(
        make_bt(stagnation_days=30.0), make_mc(12.0), make_mc(14.0), make_spp(),
        make_live(stagnation_days=18.0),
    )
    assert r_elevada["stagn_label"] == "Elevada"
    assert r_elevada["stagn_estado"] == "ALERTA"
    # stagn_live se trunca via int() antes de compararse (validator.py:478)
    # -- 18.9 trunca a 18 y sigue siendo "Elevada"; hace falta 19.0 para
    # cruzar a "Alta".
    r_alta = v.calculate_validator_score(
        make_bt(stagnation_days=30.0), make_mc(12.0), make_mc(14.0), make_spp(),
        make_live(stagnation_days=19.0),
    )
    assert r_alta["stagn_label"] == "Alta"
    assert r_alta["stagn_estado"] == "FUERA"


def test_stagnation_bands_fixed_60_120_when_bt_stagnation_zero():
    """bt_stagnation<=0 (0 o ausente) -> umbrales fijos 60/120 (validator.py:485-486)."""
    r_normal = v.calculate_validator_score(
        make_bt(stagnation_days=0.0), make_mc(12.0), make_mc(14.0), make_spp(),
        make_live(stagnation_days=60.0),
    )
    assert r_normal["stagn_label"] == "Normal"
    r_fuera = v.calculate_validator_score(
        make_bt(stagnation_days=0.0), make_mc(12.0), make_mc(14.0), make_spp(),
        make_live(stagnation_days=121.0),
    )
    assert r_fuera["stagn_label"] == "Alta"


# ── §3.13: detcount / S.Desv / flag DESV (validator.py:577-618) ────────────

def test_detcount_and_s_desv_thresholds():
    baseline = v.calculate_validator_score(
        make_bt(), make_mc(12.0), make_mc(14.0), make_spp(), make_live()
    )
    assert baseline["detcount"] == 0
    assert baseline["s_desv"] == 10
    assert baseline["desv_flag"] == "-"

    one = v.calculate_validator_score(
        make_bt(), make_mc(12.0), make_mc(14.0), make_spp(),
        make_live(win_rate=49.0),  # wr_live < bt_wr(55) - 5
    )
    assert one["detcount"] == 1
    assert one["s_desv"] == 8
    assert one["desv_flag"] == "-"

    two = v.calculate_validator_score(
        make_bt(), make_mc(12.0), make_mc(14.0), make_spp(),
        make_live(win_rate=49.0, payout_ratio=0.9),  # + payout_live < bt_payout*0.8
    )
    assert two["detcount"] == 2
    assert two["s_desv"] == 5
    assert two["desv_flag"] == "-"

    three = v.calculate_validator_score(
        make_bt(), make_mc(12.0), make_mc(14.0), make_spp(),
        make_live(win_rate=49.0, payout_ratio=0.9, profit_factor=1.1),  # + pf_live < bt_pf*0.8
    )
    assert three["detcount"] == 3
    assert three["s_desv"] == 0
    assert three["desv_flag"] == "DESV"  # thresh_desv == 3


# ── §3.14: live_vs_bt_profit_ratio, incl. redondeo ANTES de bandear ────────

@pytest.mark.parametrize("ratio_target_pct,estado", [
    (120.0, "OK"), (70.0, "OK"), (69.9, "ALERTA"), (30.0, "ALERTA"), (29.9, "FUERA"),
])
def test_live_vs_bt_profit_ratio_bands(ratio_target_pct, estado):
    """
    bt_expect=10,bt_trades=120,bt_months=12 -> bt_profit_per_month=100.
    Se ajusta expect_live para que el ratio caiga cerca del borde deseado.
    """
    live_months = 40.0 / 4.33
    expect_live = ratio_target_pct * live_months / 100.0
    r = v.calculate_validator_score(
        make_bt(expectancy=10.0, trades_total=120, months=12.0),
        make_mc(12.0), make_mc(14.0), make_spp(),
        make_live(expectancy=expect_live),
    )
    assert r["live_vs_bt_profit_status"] == estado


def test_live_vs_bt_profit_ratio_round_before_band_defect_is_pinned():
    """
    DEFECT-PIN: live_vs_bt_profit_ratio se compara DESPUES de redondearlo a
    1dp (validator.py: `live_vs_bt_profit_ratio = round(..., 1)` y LUEGO
    `if live_vs_bt_profit_ratio > 120`). Un ratio crudo de ~120.05%
    (verificado por busqueda binaria contra la funcion real:
    raw==120.04999999999998) redondea a 120.0 y por lo tanto queda del lado
    OK, NO del lado ALERTA que un umbral evaluado sobre el valor crudo
    hubiera dado. Pinneado porque es el comportamiento actual, NO porque sea
    correcto -- un EA que superó el BT en +20.05% escapa silenciosamente de
    la señal de posible sobreajuste del backtest.
    """
    r = v.calculate_validator_score(
        make_bt(expectancy=10.0, trades_total=120, months=12.0),
        make_mc(12.0), make_mc(14.0), make_spp(),
        make_live(expectancy=11.090069284064665, total_trades=100, weeks_operating=40.0),
    )
    assert r["live_vs_bt_profit_ratio"] == 120.0
    assert r["live_vs_bt_profit_status"] == "OK"  # NO "ALERTA", pese a que el crudo era >120


# ── §3.1: Veredicto 70/45, incl. el split redondeado-vs-crudo ──────────────

def test_veredicto_dd_fuera_overrides_score():
    """dd_estado==FUERA -> ELIMINAR sin importar el score (validator.py:622-623)."""
    r = v.calculate_validator_score(
        make_bt(worst_dd_1m=8.0, trades_total=120, months=12.0),
        make_mc(12.0), make_mc(14.0), make_spp(),
        make_live(total_trades=40, max_dd_pct=100.0),  # muy por encima del limite escalado
    )
    assert r["dd_estado"] == "FUERA"
    assert r["veredicto"] == "ELIMINAR"


def test_veredicto_pf_below_1_with_50plus_trades_overrides_score():
    """pf_live<1.0 y tl>=50 -> ELIMINAR sin importar el score (validator.py:624-625)."""
    r = v.calculate_validator_score(
        make_bt(worst_dd_1m=8.0, trades_total=120, months=12.0),
        make_mc(12.0), make_mc(14.0), make_spp(),
        make_live(total_trades=100, profit_factor=0.9, max_dd_pct=5.0),
    )
    assert r["pf_estado"] in ("FUERA",)
    assert r["veredicto"] == "ELIMINAR"


def test_veredicto_score_exactly_45_is_monitorear():
    """
    score==45.0 exacto (constructed: s_riesgo=5.0, s_edge=2.5, s_caracter=0,
    s_desv=10 -> (35*5+30*2.5+15*0+20*10)/10 = 45.0). tl=40 (<50) para que
    pf FUERA no dispare el override de linea 624.
    """
    bt_ = make_bt(win_rate=55.0, profit_factor=0.5, payout_ratio=1.2,
                   worst_dd_1m=8.0, trades_total=120, months=12.0)
    live_ = make_live(
        total_trades=40, weeks_operating=8.0,
        win_rate=75.0,          # FUERA (delta 20 > 15, tl<50)
        profit_factor=0.6,      # FUERA (< 0.8, tl<50) pero >= bt_pf*0.8(0.4): sin detcount
        payout_ratio=1.92,      # FUERA (+60% var, tl<50) sobre-estimado: sin detcount
        max_dd_pct=5.0,         # OK (limite escalado = 16.0)
        avg_bars_live=35.0,     # FUERA (+75%)
        max_consec_losses=10,   # FUERA
        stagnation_days=25.0,   # Alta / FUERA
    )
    r = v.calculate_validator_score(bt_, make_mc(12.0), make_mc(14.0), make_spp(), live_)
    assert r["detcount"] == 0
    assert r["score"] == 45.0
    assert r["veredicto"] == "MONITOREAR"


def test_veredicto_score_44_875_rounds_down_and_is_eliminar():
    """
    Un solo escalon por debajo de 45.0 en la grilla de 0.125 (44.875,
    redondea a 44.9): ambos el score crudo y el redondeado caen del mismo
    lado (<45) -- ELIMINAR consistente, sin split.
    """
    bt_ = make_bt(win_rate=55.0, profit_factor=0.5, payout_ratio=1.2,
                   worst_dd_1m=8.0, trades_total=120, months=12.0)
    live_ = make_live(
        total_trades=40, weeks_operating=8.0,
        win_rate=75.0, profit_factor=0.6, payout_ratio=1.92,
        max_dd_pct=20.0,        # ALERTA (limite=16.0, 1.5x=24.0)
        avg_bars_live=27.0,     # ALERTA (+35%)
        max_consec_losses=7,    # ALERTA (cl_bt=5, 1.5x=7.5)
        stagnation_days=25.0,
    )
    r = v.calculate_validator_score(bt_, make_mc(12.0), make_mc(14.0), make_spp(), live_)
    assert r["detcount"] == 0
    assert r["score"] == 44.9
    assert r["veredicto"] == "ELIMINAR"


def test_veredicto_score_exactly_70_is_continuar_no_rounding_split_found():
    """
    Se investigo la hipotesis (por analogia con el split redondeado-vs-crudo
    confirmado en CP3, ver test_char_incubation.py e
    incubation_validator.py:1165 vs :1170) de que pudiera existir un caso
    donde result["score"]==70.0 pero veredicto=="MONITOREAR" en
    validator.py.

    INVESTIGADO Y REFUTADO para validator.py: s_riesgo/s_edge/s_caracter son
    siempre sumas exactas de pts(estado) in {0,5,10} por peso/100 (weights
    multiplos de 5), y s_desv in {0,5,8,10} -- por construccion algebraica
    (verificado exhaustivamente con fractions.Fraction sobre las 3**9*4
    combinaciones de estados) el score crudo SIEMPRE cae en una grilla
    exacta de multiplos de 0.125, representable sin error en binario. El
    punto de grilla inmediatamente por debajo de 70.0 es 69.875 (redondea a
    69.9, mismo lado que el crudo). No existe combinacion de estados que
    produzca un crudo en (69.875, 70.0) -- el split que SI existe y esta
    confirmado es el de CP3 (ver test_char_incubation.py). Este test fija
    el comportamiento real: consistencia, no defecto, en este punto exacto.
    """
    bt_ = make_bt(win_rate=55.0, profit_factor=1.5, payout_ratio=1.2,
                   worst_dd_1m=8.0, trades_total=120, months=12.0)
    live_ = make_live(
        total_trades=100, weeks_operating=20.0,
        win_rate=55.0,           # OK
        profit_factor=1.3,       # ALERTA (tl>=50: [1.0, bt_pf))
        payout_ratio=1.2,        # OK
        max_dd_pct=5.0,          # OK
        avg_bars_live=35.0,      # FUERA (+75%)
        max_consec_losses=10,    # FUERA
        stagnation_days=5.0,     # OK
    )
    r = v.calculate_validator_score(bt_, make_mc(12.0), make_mc(14.0), make_spp(), live_)
    assert r["detcount"] == 0
    assert r["score"] == 70.0
    assert r["veredicto"] == "CONTINUAR"  # consistente -- NO hay split aqui


# ── §1.1 ⚠ / §6: fuga de setdefault en _nd_result (DEFECT-PIN) ─────────────

def test_nd_result_setdefault_leak_at_gate_2_is_pinned():
    """
    DEFECT-PIN: cuando el gate #2 (validator.py:500-555) dispara SIN DATOS
    porque UN SOLO estado escalonado quedo N/D (aqui: dd_estado, porque
    bt.worst_dd_1m<=0 y no hay fallback MC), _nd_result() fuerza TODOS los
    9 estados escalonados a "N/D" -- incluso los que ya se habian calculado
    correctamente como OK/ALERTA/FUERA -- pero el setdefault() que sigue NO
    puede tocar las claves numericas ya asignadas (wr_live, wr_delta,
    pf_live, payout_var, bars_var, freq_pct, edge_erosion, dd_live,
    stagn_live...). El resultado: numeros reales y confiados conviviendo
    con "N/D" en sus estados companeros. Pinneado porque es el
    comportamiento actual (validator.py:163-189, known-issues.md §6), NO
    porque sea correcto.
    """
    r = v.calculate_validator_score(
        make_bt(worst_dd_1m=0.0), make_mc(None), make_mc(None), make_spp(),
        make_live(),
    )
    assert r["veredicto"] == "SIN DATOS"
    assert r["sin_datos"] is True
    assert r["missing"] == ["dd_estado", "bt.worst_dd_1m"]

    # Todos los estados escalonados fueron forzados a N/D...
    for key in ("wr_estado", "pf_estado", "payout_estado", "dd_estado",
                "consec_estado", "bars_estado", "freq_estado", "edge_estado",
                "stagn_estado", "stagn_label", "dd_method", "consec_ratio"):
        assert r[key] == "N/D"

    # ...pero los NUMEROS crudos detras de esos estados sobreviven intactos,
    # como si el sistema todavia tuviera confianza en ellos.
    assert r["wr_live"] == 55.0
    assert r["wr_delta"] == 0.0
    assert r["pf_live"] == 1.5
    assert r["payout_var"] == 0.0
    assert r["bars_var"] == 0.0
    assert r["freq_pct"] == 108.2
    assert r["edge_erosion"] == 0.0
    assert r["dd_live"] == 5.0
    assert r["stagn_live"] == 10.0

    # dd_limit SI se re-blanquea explicitamente a None (:188) -- aqui ya
    # era None porque ninguna rama de DD se disparo (bt_worst_dd_1m<=0 y
    # ambos MC ausentes), asi que no hay numero confiado que mostrar.
    assert r["dd_limit"] is None

    # Las categorias/score/live_vs_bt_profit_ratio SI quedan correctamente en
    # None -- todavia no se habian calculado en este punto del flujo.
    for key in ("s_riesgo", "s_edge", "s_caracter", "s_desv", "detcount", "live_vs_bt_profit_ratio"):
        assert r[key] is None
    assert r["live_vs_bt_profit_status"] == "N/D"


# ── §1.2: get_all_validator_results -- orden de sort y skips ───────────────

def _parsed_data(trades):
    return {"closed_trades": trades}


def test_get_all_validator_results_skips_inactive_and_missing_magic():
    config = {
        "mappings": {
            "Inactive": {"magic": "1", "alias": "Inactive", "instrument": "EURUSD",
                         "capital": 10000.0, "active": False},
            "NoMagic": {"magic": "", "alias": "NoMagic", "instrument": "EURUSD",
                        "capital": 10000.0, "active": True},
            "Kept": {"magic": "42", "alias": "Kept", "instrument": "EURUSD",
                     "capital": 10000.0, "active": True},
        }
    }
    rows = v.get_all_validator_results(_parsed_data([]), config, store={})
    names = [r["ea_name"] for r in rows]
    assert names == ["Kept"]


def test_get_all_validator_results_sort_score_none_after_scored_but_before_no_bt():
    """
    _sort_key (validator.py:808-812): has_bt primero; dentro de has_bt,
    orden descendente por score, con score=None mapeado a -(-1)=1 -- es
    decir, un EA con BT pero en SIN DATOS (score=None) se ordena DESPUES de
    cualquier EA con score real, pero ANTES (has_bt=0 < has_bt=1 en la
    tupla de sort) de cualquier EA sin BT en absoluto.
    """
    config = {
        "mappings": {
            "Scored": {"magic": "1", "alias": "Scored", "instrument": "EURUSD",
                       "capital": 10000.0, "active": True},
            "SinDatos": {"magic": "2", "alias": "SinDatos", "instrument": "EURUSD",
                         "capital": 10000.0, "active": True},
            "NoBt": {"magic": "3", "alias": "NoBt", "instrument": "EURUSD",
                     "capital": 10000.0, "active": True},
        }
    }
    store = {
        "1": {"bt": make_bt(worst_dd_1m=8.0), "mc_retest": make_mc(12.0),
              "mc_trades": make_mc(14.0), "spp": make_spp(), "timeframe": "H1"},
        # bt presente pero incompleto (falta trades_total) -> SIN DATOS (score None)
        "2": {"bt": {"win_rate": 55.0}, "mc_retest": make_mc(12.0),
              "mc_trades": make_mc(14.0), "spp": make_spp(), "timeframe": "H1"},
        # sin entrada bt en absoluto -> has_bt=False
    }
    trades_scored = [
        {"comment": "Scored", "net_pnl": 100.0, "close_time": "2026-01-02T12:00:00",
         "direction": "buy", "symbol": "EURUSD", "duration_hours": 1.0}
        for _ in range(60)
    ]
    trades_sindatos = [
        {"comment": "SinDatos", "net_pnl": 100.0, "close_time": "2026-01-02T12:00:00",
         "direction": "buy", "symbol": "EURUSD", "duration_hours": 1.0}
        for _ in range(60)
    ]
    rows = v.get_all_validator_results(
        _parsed_data(trades_scored + trades_sindatos), config, store
    )
    names = [r["ea_name"] for r in rows]
    assert names == ["Scored", "SinDatos", "NoBt"]
    assert rows[0]["analysis"]["score"] is not None
    assert rows[1]["analysis"]["score"] is None
    assert rows[1]["has_bt"] is True
    assert rows[2]["has_bt"] is False
    assert rows[2]["analysis"] is None
