"""
test_diff_metrics.py - Capa 3: pruebas diferenciales contra un oraculo INDEPENDIENTE.

Para cada metrica se calcula la MISMA cantidad por un camino que NO copia la
expresion de produccion (metrics.py / incubation_validator.py), sino que la
deriva de su definicion estandar (o de una libreria externa ya validada:
empyrical, scipy). Ambos valores se comparan con una tolerancia explicita,
justificada en cada constante de tolerancia. Toda divergencia que exceda la
tolerancia es un HALLAZGO: el test sigue en verde via
`pytest.mark.xfail(strict=True, ...)` para que quede documentado sin romper
el suite.

Este archivo NO modifica produccion. Solo observa.
"""

import math
import statistics

import numpy as np
import pytest
import scipy.stats as st
import empyrical

import metrics
import incubation_validator
from conftest import make_trades, make_config


# ─────────────────────────────────────────────────────────────────────────
# TOLERANCIAS — cada una justificada por el redondeo que hace produccion.
# ─────────────────────────────────────────────────────────────────────────

# Sharpe: metrics._calc_sharpe redondea a 2dp (metrics.py:185). numpy vs una
# reimplementacion via empyrical puede diferir en el ULP mas bajo; 2dp de
# redondeo more que cubre eso.
TOL_SHARPE = 0.006

# max_dd_pct: metrics._calc_max_drawdown redondea internamente a 4dp
# (metrics.py:159) y calculate_ea_metrics vuelve a redondear ese resultado a
# 2dp (metrics.py:460) — doble redondeo. Ademas el calculo interno opera
# sobre el equity_curve YA redondeado a 2dp punto a punto (metrics.py:95),
# no sobre el pnl crudo, así que el error de redondeo puede acumularse
# ligeramente a lo largo de la curva. 0.05 puntos porcentuales cubre ambas
# fuentes con margen.
TOL_DD_PCT = 0.05

# max_dd_dollar: redondeado una sola vez a 2dp (metrics.py:459).
TOL_DD_DOLLAR = 0.03

# profit_factor / win_rate / expectancy / payout_ratio: metrics.py redondea a
# 2dp (net_profit/total_trades, win_rate, profit_factor, payout_ratio todos a
# 2dp via fmt_pf/round() -- metrics.py:431 a nivel EA, :578 a nivel portfolio).
TOL_PF = 0.01
TOL_WIN_RATE = 0.01
TOL_EXPECTANCY = 0.01
TOL_PAYOUT = 0.01

# SQN: metrics._calc_sqn redondea a 2dp (metrics.py:219).
TOL_SQN = 0.01

# p-valor binomial: math.comb es aritmetica exacta de precision entera hasta
# el ultimo paso (potencias de float); scipy.stats.binom.cdf usa su propia
# ruta interna (log-gamma / suma acumulada). Ambos deberian coincidir a
# precision de double casi completa.
TOL_BINOMIAL_P = 1e-9


# ─────────────────────────────────────────────────────────────────────────
# ORACULOS INDEPENDIENTES
# ─────────────────────────────────────────────────────────────────────────

def oracle_sharpe_empyrical(net_pnl_list):
    """Sharpe por libreria externa: empyrical.sharpe_ratio con
    period='daily', annualization=1 reduce la formula a mean/std(ddof=1) sin
    anualizar -- la misma definicion textbook de Sharpe "por trade" que usa
    metrics.py, pero calculada por una libreria de terceros ya validada
    (quantopian/pyfolio lineage), no por una reimplementacion manual de la
    expresion de metrics.py.
    """
    return empyrical.sharpe_ratio(np.array(net_pnl_list, dtype=float), period="daily", annualization=1)


def oracle_max_drawdown_hand(net_pnl_list, capital):
    """Derivacion manual, vectorizada con numpy, de la definicion de max DD
    de este repo: equity = P&L acumulado desde 0 (no equity absoluta), pico
    medido desde el piso 0.0, denominador porcentual = capital + pico.

    Es una reimplementacion DISTINTA en forma (vectorizada, sin el loop de
    metrics._calc_max_drawdown, sin pasar por el equity_curve
    pre-redondeado de metrics._build_equity_curve) que solo comparte la
    definicion matematica documentada en docs/metrics-formulas.md (seccion
    6, "Drawdown (DD)") y docs/known-issues.md §7, no la expresion linea
    por linea de produccion.

    Retorna (max_dd_dollar, max_dd_pct) sin redondear.
    """
    pnl = np.array([0.0] + list(net_pnl_list), dtype=float)
    cum = np.cumsum(pnl)  # equity relativa a 0, incluye el ancla inicial 0.0
    peak = np.maximum.accumulate(cum)  # pico nunca baja de 0.0 (ancla)
    dd_dollar = peak - cum
    peak_abs = capital + peak
    dd_pct = np.where(peak_abs > 0, dd_dollar / peak_abs * 100, 0.0)
    return float(dd_dollar.max()), float(dd_pct.max())


def oracle_max_drawdown_empyrical_exact(net_pnl_list, capital):
    """Cross-check del oraculo manual contra empyrical.max_drawdown.

    empyrical.max_drawdown espera una serie de RETORNOS periodo-a-periodo
    que compone multiplicativamente (cumprod(1+r)). Para que coincida
    EXACTAMENTE con la definicion de este repo (peak_abs = capital +
    peak_pnl, un piso que flota con el pico de equity) hay que reconstruir
    los retornos a partir de la equity absoluta real:

        equity_abs_i = capital + cumsum(pnl)_i   (con ancla 0 en i=0)
        r_i = equity_abs_i / equity_abs_{i-1} - 1

    Con esos retornos, cumprod(1+r) reconstruye exactamente equity_abs, y
    como capital es constante, running_max(equity_abs) = capital +
    running_max(pnl) = peak_abs de produccion. Es una identidad algebraica,
    no una aproximacion: para cualquier serie de trades donde equity_abs
    nunca toca 0, esto coincide con la definicion del repo dentro del
    margen de punto flotante.
    """
    pnl = np.array([0.0] + list(net_pnl_list), dtype=float)
    cum = np.cumsum(pnl)
    equity_abs = capital + cum
    returns = equity_abs[1:] / equity_abs[:-1] - 1.0
    dd = empyrical.max_drawdown(returns)
    return abs(dd) * 100


def oracle_max_drawdown_empyrical_naive(net_pnl_list, capital):
    """Uso 'ingenuo' de empyrical.max_drawdown: retornos simples pnl/capital
    (denominador FIJO), sin reconstruir la equity absoluta. Esta es la forma
    en la que alguien integraria empyrical sin conocer el detalle de que el
    denominador de este repo flota con el pico (capital + peak_pnl).

    Documentado para explicar POR QUE diverge de la definicion del repo: al
    alimentar retornos lineales pnl/capital a una funcion que compone
    multiplicativamente (cumprod), el resultado deja de ser aritmeticamente
    equivalente al drawdown aditivo/piso-flotante de metrics.py en cuanto
    los movimientos por trade dejan de ser insignificantes frente al
    capital. No es un bug de empyrical ni de metrics.py -- es un choque de
    convenciones (retorno simple vs. retorno compuesto reconstruido).
    """
    returns = np.array(net_pnl_list, dtype=float) / capital
    dd = empyrical.max_drawdown(returns)
    return abs(dd) * 100


def oracle_profit_factor(net_pnl_list):
    """PF textbook: suma de ganancias / |suma de perdidas|.

    Se filtra con < 0 / > 0 (estrictos), NO con la particion <= 0 que usa
    metrics.py para wins/losses (metrics.py:353-354, donde net_pnl==0 cuenta
    como perdida). Da igual para la SUMA: un trade en 0 no aporta nada ni a
    gross_profit ni a gross_loss sea cual sea el filtro que lo excluya, asi
    que la formula converge al mismo profit_factor pese a particionar con un
    criterio distinto -- prueba independiente de que el trato de net_pnl==0
    no contamina el profit_factor reportado.
    """
    profits = sum(p for p in net_pnl_list if p > 0)
    losses = sum(p for p in net_pnl_list if p < 0)
    if losses != 0:
        return profits / abs(losses)
    return float("inf") if profits > 0 else 0.0


def oracle_payout_ratio(net_pnl_list):
    """Payout ratio textbook = avg(ganadores) / |avg(perdedores)|, particion
    ESTRICTA (> 0 / < 0), a diferencia de la particion <= 0 que metrics.py
    usa para wins/losses (metrics.py:353-354/366/373-377, donde net_pnl==0
    cuenta como perdida). A DIFERENCIA de profit_factor (donde un trade en
    0 no aporta a la suma sea cual sea el filtro que lo excluya, asi que las
    dos particiones convergen), payout_ratio SI diverge: un trade en 0
    infla el DENOMINADOR de conteo (losing_trades) sin aportar magnitud,
    encogiendo |avg_loss| y por lo tanto inflando el ratio reportado. Este
    oraculo materializa la definicion textbook para medir esa divergencia
    -- ver test_payout_ratio_zero_pnl_trade_inflation_defect_pin en
    test_char_metrics.py para la caracterizacion puntual con numeros
    exactos.
    """
    wins = [p for p in net_pnl_list if p > 0]
    losses = [p for p in net_pnl_list if p < 0]
    avg_win = statistics.mean(wins) if wins else 0.0
    avg_loss = statistics.mean(losses) if losses else 0.0
    if avg_loss != 0:
        return avg_win / abs(avg_loss)
    return float("inf") if avg_win > 0 else 0.0


def oracle_expectancy(net_pnl_list):
    """Expectancy textbook = P&L promedio por trade = mean(net_pnl)."""
    if not net_pnl_list:
        return 0.0
    return statistics.mean(net_pnl_list)


def oracle_win_rate(net_pnl_list):
    """Win rate textbook = trades con P&L > 0 / total * 100."""
    if not net_pnl_list:
        return 0.0
    wins = sum(1 for p in net_pnl_list if p > 0)
    return wins / len(net_pnl_list) * 100


def oracle_sqn_stdlib(net_pnl_list):
    """SQN via la libreria estandar `statistics` (mean/stdev con ddof=1 por
    definicion de statistics.stdev), en vez de numpy -- libreria distinta a
    la que usa metrics._calc_sqn (np.mean/np.std(ddof=1)), misma formula de
    Van Tharp SIN el cap de sqrt(100): sqrt(n) * mean / stdev.
    """
    n = len(net_pnl_list)
    if n < 2:
        return None
    mean_r = statistics.mean(net_pnl_list)
    std_r = statistics.stdev(net_pnl_list)
    if std_r == 0:
        return None
    return math.sqrt(n) * mean_r / std_r


def oracle_sqn_tharp_capped(net_pnl_list):
    """SQN segun la convencion capada de la comunidad (atribucion a Tharp no
    verificada): sqrt(min(n, 100)) * mean / stdev.

    docs/known-issues.md §7 y docs/research/prior-art.md §2.2 documentan que
    la atribucion de este cap a Van Tharp no pudo confirmarse ni descartarse
    contra una fuente primaria (vantharpinstitute.com 403, libro paywalled);
    el cap rastrea solo a paráfrasis de terceros sin cita. metrics.py usa
    sqrt(n) sin limite. Este oraculo materializa la convencion CON el cap
    para medir la divergencia frente a esa convencion en un N grande.
    """
    n = len(net_pnl_list)
    if n < 2:
        return None
    mean_r = statistics.mean(net_pnl_list)
    std_r = statistics.stdev(net_pnl_list)
    if std_r == 0:
        return None
    return math.sqrt(min(n, 100)) * mean_r / std_r


def oracle_binomial_p_value_scipy(wins, n, p):
    """Oraculo exacto: scipy.stats.binom.cdf. Libreria distinta, misma
    definicion (CDF binomial acumulada P(X <= wins)), sin copiar la
    expresion de math.comb de incubation_validator._binomial_p_value.
    """
    return float(st.binom.cdf(wins, n, p))


def doc_binomial_normal_approximation(wins, n, p):
    """Formula LITERAL que documenta docs/metrics-formulas.md:386-391 para
    el 'camino sin scipy': aproximacion normal a la binomial,
    z = (wins - N*p) / sqrt(N*p*(1-p)); p_valor = 0.5*(1 + erf(z/sqrt(2))).

    Este oraculo NO representa el codigo real (que es puro math.comb, sin
    scipy ni aproximacion normal -- ver incubation_validator.py:252-268 y su
    propio docstring). Se usa unicamente para demostrar que la formula que
    el doc afirma implementar NO es la que ejecuta el codigo.
    """
    if n <= 0:
        return 1.0
    mean = n * p
    var = n * p * (1 - p)
    if var <= 0:
        return 1.0 if wins >= mean else 0.0
    z = (wins - mean) / math.sqrt(var)
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


# ─────────────────────────────────────────────────────────────────────────
# FIXTURES DE FORMA DE ENTRADA (cobertura requerida)
# ─────────────────────────────────────────────────────────────────────────

CAPITAL = 10000.0

SHAPES = {
    "all_wins": [100.0, 250.0, 50.0, 400.0, 120.0],
    "all_losses": [-200.0, -150.0, -50.0, -300.0, -80.0],
    "mixed": [500.0, -200.0, 800.0, -1500.0, 300.0, -100.0, 1200.0],
    "zero_pnl_trades": [100.0, 0.0, -50.0, 0.0, 200.0, -30.0],
    "two_trades": [300.0, -100.0],
    "degenerate_variance": [100.0, 100.0, 100.0, 100.0],  # std == 0
}


def _pnls_for(shape_name):
    return SHAPES[shape_name]


def _ea_metrics_for(shape_name, capital=CAPITAL):
    """Corre la API PUBLICA calculate_ea_metrics sobre la forma dada."""
    pnls = _pnls_for(shape_name)
    trades = make_trades(pnls)
    config = make_config(capital=capital)
    return metrics.calculate_ea_metrics("MyEA", trades, config)


# ─────────────────────────────────────────────────────────────────────────
# SHARPE
# ─────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("shape_name", ["mixed", "all_wins", "all_losses", "zero_pnl_trades", "two_trades"])
def test_sharpe_matches_empyrical_oracle(shape_name):
    """Sharpe reportado por la API publica vs. empyrical.sharpe_ratio,
    respetando el guard de coeficiente-de-variacion (MIN_COEFFICIENT_OF_VARIATION)
    de metrics.py: si el guard dispara, la API devuelve None y NO se compara
    numericamente contra el oraculo (que no tiene ese guard).
    """
    pnls = _pnls_for(shape_name)
    m = _ea_metrics_for(shape_name)

    arr = np.array(pnls, dtype=float)
    mean_r, std_r = float(np.mean(arr)), float(np.std(arr, ddof=1))
    guard_triggers = std_r <= abs(mean_r) * metrics.MIN_COEFFICIENT_OF_VARIATION

    if guard_triggers:
        assert m["sharpe_ratio"] is None
        return

    oracle = oracle_sharpe_empyrical(pnls)
    assert m["sharpe_ratio"] is not None
    assert abs(m["sharpe_ratio"] - oracle) <= TOL_SHARPE


def test_sharpe_cv_guard_is_a_documented_repo_deviation():
    """degenerate_variance: std==0 exactamente. La definicion ESTANDAR de
    Sharpe (mean/std) es indefinida (division por cero) aqui; empyrical
    devuelve NaN o 0.0 dependiendo de la implementacion. metrics.py, en vez
    de propagar esa indefinicion, aplica MIN_COEFFICIENT_OF_VARIATION=0.01 y
    devuelve None de forma explicita.

    Este test PIN-ea esa desviacion documentada (no es un hallazgo nuevo:
    docs/metrics-formulas.md seccion 9 "Sharpe Ratio" ya la nombra, mismo
    guard que SQN) -- el oraculo "estandar" no produce un numero comparable,
    asi que aqui solo afirmamos el comportamiento repo-especifico.
    """
    pnls = _pnls_for("degenerate_variance")
    m = _ea_metrics_for("degenerate_variance")
    arr = np.array(pnls, dtype=float)
    std_r = float(np.std(arr, ddof=1))
    assert std_r == 0.0  # confirma que estamos en el caso realmente degenerado
    assert m["sharpe_ratio"] is None  # desviacion repo-especifica vs. la formula estandar (indefinida)


# ─────────────────────────────────────────────────────────────────────────
# MAX DRAWDOWN
# ─────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "shape_name",
    ["mixed", "all_wins", "all_losses", "zero_pnl_trades", "two_trades", "degenerate_variance"],
)
def test_max_drawdown_matches_hand_derived_oracle(shape_name):
    """max_dd_dollar / max_dd_pct de la API publica vs. una reimplementacion
    manual vectorizada (oracle_max_drawdown_hand) que comparte la DEFINICION
    documentada (docs/metrics-formulas.md seccion 6) pero no la expresion
    linea-por-linea de metrics._calc_max_drawdown.
    """
    pnls = _pnls_for(shape_name)
    m = _ea_metrics_for(shape_name)

    oracle_dollar, oracle_pct = oracle_max_drawdown_hand(pnls, CAPITAL)

    assert abs(m["max_dd_dollar"] - oracle_dollar) <= TOL_DD_DOLLAR
    assert abs(m["max_dd_pct"] - oracle_pct) <= TOL_DD_PCT


def test_max_drawdown_single_trade_no_prior_peak():
    """Un solo trade: si es ganador, el pico avanza junto con el pnl y el DD
    nunca es positivo (peak == pnl en todo momento) -> DD 0. Si es
    perdedor, el pico se queda en el ancla 0.0 y el DD es exactamente
    |pnl|/capital. Caso limite explicito de la cobertura requerida.
    """
    trades_win = make_trades([500.0])
    m_win = metrics.calculate_ea_metrics("MyEA", trades_win, make_config(capital=CAPITAL))
    oracle_dollar_win, oracle_pct_win = oracle_max_drawdown_hand([500.0], CAPITAL)
    assert m_win["max_dd_dollar"] == pytest.approx(oracle_dollar_win, abs=TOL_DD_DOLLAR)
    assert m_win["max_dd_pct"] == pytest.approx(oracle_pct_win, abs=TOL_DD_PCT)
    assert m_win["max_dd_dollar"] == 0.0

    trades_loss = make_trades([-500.0])
    m_loss = metrics.calculate_ea_metrics("MyEA", trades_loss, make_config(capital=CAPITAL))
    oracle_dollar_loss, oracle_pct_loss = oracle_max_drawdown_hand([-500.0], CAPITAL)
    assert m_loss["max_dd_dollar"] == pytest.approx(oracle_dollar_loss, abs=TOL_DD_DOLLAR)
    assert m_loss["max_dd_pct"] == pytest.approx(oracle_pct_loss, abs=TOL_DD_PCT)


@pytest.mark.parametrize("shape_name", ["mixed", "all_losses", "two_trades"])
def test_max_drawdown_cross_check_empyrical_exact_reconstruction(shape_name):
    """Segundo cruce independiente: alimentar empyrical.max_drawdown con los
    retornos RECONSTRUIDOS desde la equity absoluta (ver docstring de
    oracle_max_drawdown_empyrical_exact) reproduce la definicion del repo
    con precision de punto flotante -- es una identidad algebraica, no una
    aproximacion, y por eso coincide en TODAS las formas, con o sin
    crecimiento de equity.
    """
    pnls = _pnls_for(shape_name)
    m = _ea_metrics_for(shape_name)
    oracle_pct = oracle_max_drawdown_empyrical_exact(pnls, CAPITAL)
    assert abs(m["max_dd_pct"] - oracle_pct) <= TOL_DD_PCT


def test_max_drawdown_naive_empyrical_returns_diverge_from_repo_definition():
    """HALLAZGO EXPLICATIVO (no es un bug de metrics.py): si en vez de
    reconstruir la equity absoluta uno alimenta empyrical.max_drawdown con
    retornos simples pnl/capital (denominador FIJO), el resultado NO
    coincide con la definicion de este repo (denominador = capital +
    pico_de_equity, que FLOTA hacia arriba a medida que la cuenta crece).

    Sobre la forma 'mixed' (que sí tiene crecimiento de equity antes del
    drawdown maximo), la discrepancia es de mas de 1 punto porcentual --
    muy por encima de TOL_DD_PCT. Esto no es una discrepancia entre el repo
    y un oraculo valido: es la demostracion de que un integrador que use
    empyrical.max_drawdown "a lo obvio" (retorno simple) obtendria un
    numero DISTINTO del que reporta metrics.py, porque no son la misma
    definicion de drawdown porcentual. Se deja como assert explicito (no
    xfail) porque la divergencia es la conclusion esperada y documentada,
    no una expectativa de igualdad que falla.
    """
    pnls = _pnls_for("mixed")
    m = _ea_metrics_for("mixed")
    naive_pct = oracle_max_drawdown_empyrical_naive(pnls, CAPITAL)
    divergence = abs(m["max_dd_pct"] - naive_pct)
    assert divergence > 1.0, (
        f"se esperaba una divergencia notable repo={m['max_dd_pct']} vs "
        f"naive-empyrical={naive_pct}, pero fue solo {divergence}"
    )


# ─────────────────────────────────────────────────────────────────────────
# PROFIT FACTOR / EXPECTANCY / WIN RATE
# ─────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "shape_name",
    ["all_wins", "all_losses", "mixed", "zero_pnl_trades", "two_trades", "degenerate_variance"],
)
def test_profit_factor_matches_textbook_oracle(shape_name):
    """profit_factor de la API publica vs. PF = sum(ganancias)/|sum(perdidas)|
    derivado directamente de la definicion textbook (filtro > 0 / < 0
    estricto, no el <= 0 que usa metrics.py para la particion win/loss)."""
    pnls = _pnls_for(shape_name)
    m = _ea_metrics_for(shape_name)
    oracle = oracle_profit_factor(pnls)

    if oracle == float("inf"):
        assert m["profit_factor"] == "∞"
    else:
        assert m["profit_factor"] != "∞"
        assert abs(m["profit_factor"] - oracle) <= TOL_PF


@pytest.mark.parametrize(
    "shape_name",
    ["all_wins", "all_losses", "mixed", "zero_pnl_trades", "two_trades", "degenerate_variance"],
)
def test_expectancy_matches_textbook_oracle(shape_name):
    """expectancy de la API publica vs. mean(net_pnl) via statistics.mean
    (libreria estandar, no numpy ni la expresion net_profit/total_trades de
    metrics.py)."""
    pnls = _pnls_for(shape_name)
    m = _ea_metrics_for(shape_name)
    oracle = oracle_expectancy(pnls)
    assert abs(m["expectancy"] - oracle) <= TOL_EXPECTANCY


@pytest.mark.parametrize(
    "shape_name",
    ["all_wins", "all_losses", "mixed", "zero_pnl_trades", "two_trades", "degenerate_variance"],
)
def test_win_rate_matches_textbook_oracle(shape_name):
    """win_rate de la API publica vs. conteo directo de trades con P&L > 0.
    Confirma tambien, indirectamente, que net_pnl == 0 NO se cuenta como
    ganador (zero_pnl_trades)."""
    pnls = _pnls_for(shape_name)
    m = _ea_metrics_for(shape_name)
    oracle = oracle_win_rate(pnls)
    assert abs(m["win_rate"] - oracle) <= TOL_WIN_RATE


@pytest.mark.parametrize(
    "shape_name",
    ["all_wins", "all_losses", "mixed", "two_trades", "degenerate_variance"],
)
def test_payout_ratio_matches_textbook_oracle(shape_name):
    """payout_ratio de la API publica vs. avg(ganadores)/|avg(perdedores)|
    derivado directamente de la definicion textbook (oracle_payout_ratio,
    particion > 0/< 0 estricta). "zero_pnl_trades" se excluye A PROPOSITO
    de este parametrize: ese shape SI diverge del oraculo textbook porque
    metrics.py cuenta net_pnl==0 como perdida (metrics.py:354), lo que
    infla artificialmente el payout_ratio reportado -- ver
    test_payout_ratio_zero_pnl_trade_inflation_defect_pin en
    test_char_metrics.py para esa caracterizacion puntual. payout_ratio no
    tenia NINGUN oraculo diferencial antes de este test."""
    pnls = _pnls_for(shape_name)
    m = _ea_metrics_for(shape_name)
    oracle = oracle_payout_ratio(pnls)

    if oracle == float("inf"):
        assert m["payout_ratio"] == "∞"
    else:
        assert m["payout_ratio"] != "∞"
        assert abs(m["payout_ratio"] - oracle) <= TOL_PAYOUT


def test_zero_pnl_trade_counts_as_loss_not_win():
    """Caracterizacion puntual del criterio <= 0 (metrics.py:354): de los 6
    trades en zero_pnl_trades, 2 valen exactamente 0.0 y deben contarse como
    perdedores, no ganadores -- verificado contra el conteo manual."""
    pnls = _pnls_for("zero_pnl_trades")
    m = _ea_metrics_for("zero_pnl_trades")
    manual_wins = sum(1 for p in pnls if p > 0)
    manual_losses = sum(1 for p in pnls if p <= 0)
    assert m["winning_trades"] == manual_wins
    assert m["losing_trades"] == manual_losses
    assert 0.0 in pnls  # confirma que la forma realmente ejercita el caso limite


# ─────────────────────────────────────────────────────────────────────────
# PORTFOLIO -- misma cobertura diferencial que EA-level (F4)
# ─────────────────────────────────────────────────────────────────────────
#
# calculate_portfolio_metrics (metrics.py:481-625) NO delega a
# calculate_ea_metrics: DUPLICA la aritmetica de win_rate/profit_factor/
# payout_ratio/expectancy textualmente aparte (metrics.py:503-543), separada
# de la version EA-level (metrics.py:340-390). Antes de estos tests solo se
# verificaba la FORMA/tipo de la salida de calculate_portfolio_metrics (ver
# test_calculate_portfolio_metrics_same_41_key_contract en
# test_char_metrics.py) -- ningun test comparaba VALORES contra un oraculo
# independiente para el bloque de formula propio del portfolio. Un bug
# introducido SOLO en metrics.py:514 (portfolio win_rate) mientras
# metrics.py:364 (EA win_rate) sigue correcto sobrevivia en silencio. Estos
# tests reusan los MISMOS oraculos independientes de arriba (no copian la
# expresion de produccion) contra calculate_portfolio_metrics en vez de
# calculate_ea_metrics.

def _portfolio_metrics_for(shape_name, capital=CAPITAL, comment="EA1"):
    """Corre la API PUBLICA calculate_portfolio_metrics sobre la forma dada.
    El capital de la EA es irrelevante para win_rate/profit_factor/
    payout_ratio/expectancy (solo afecta max_dd/ret_dd, no cubiertos aqui)."""
    pnls = _pnls_for(shape_name)
    trades = make_trades(pnls, comment=comment)
    config = make_config(ea_name=comment, capital=capital)
    return metrics.calculate_portfolio_metrics(trades, config)


@pytest.mark.parametrize(
    "shape_name",
    ["all_wins", "all_losses", "mixed", "zero_pnl_trades", "two_trades", "degenerate_variance"],
)
def test_portfolio_win_rate_matches_textbook_oracle(shape_name):
    pnls = _pnls_for(shape_name)
    m = _portfolio_metrics_for(shape_name)
    oracle = oracle_win_rate(pnls)
    assert abs(m["win_rate"] - oracle) <= TOL_WIN_RATE


@pytest.mark.parametrize(
    "shape_name",
    ["all_wins", "all_losses", "mixed", "zero_pnl_trades", "two_trades", "degenerate_variance"],
)
def test_portfolio_profit_factor_matches_textbook_oracle(shape_name):
    pnls = _pnls_for(shape_name)
    m = _portfolio_metrics_for(shape_name)
    oracle = oracle_profit_factor(pnls)

    if oracle == float("inf"):
        assert m["profit_factor"] == "∞"
    else:
        assert m["profit_factor"] != "∞"
        assert abs(m["profit_factor"] - oracle) <= TOL_PF


@pytest.mark.parametrize(
    "shape_name",
    ["all_wins", "all_losses", "mixed", "two_trades", "degenerate_variance"],
)
def test_portfolio_payout_ratio_matches_textbook_oracle(shape_name):
    """"zero_pnl_trades" se excluye por la misma razon documentada en
    test_payout_ratio_matches_textbook_oracle (EA-level, mas arriba): la
    inflacion por net_pnl==0 contando como perdida es un defecto conocido,
    no una divergencia de este test."""
    pnls = _pnls_for(shape_name)
    m = _portfolio_metrics_for(shape_name)
    oracle = oracle_payout_ratio(pnls)

    if oracle == float("inf"):
        assert m["payout_ratio"] == "∞"
    else:
        assert m["payout_ratio"] != "∞"
        assert abs(m["payout_ratio"] - oracle) <= TOL_PAYOUT


@pytest.mark.parametrize(
    "shape_name",
    ["all_wins", "all_losses", "mixed", "zero_pnl_trades", "two_trades", "degenerate_variance"],
)
def test_portfolio_expectancy_matches_textbook_oracle(shape_name):
    pnls = _pnls_for(shape_name)
    m = _portfolio_metrics_for(shape_name)
    oracle = oracle_expectancy(pnls)
    assert abs(m["expectancy"] - oracle) <= TOL_EXPECTANCY


# ─────────────────────────────────────────────────────────────────────────
# SQN
# ─────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("shape_name", ["mixed", "all_wins", "all_losses", "zero_pnl_trades", "two_trades"])
def test_sqn_matches_stdlib_oracle(shape_name):
    """sqn de la API publica vs. sqrt(n)*mean/stdev calculado con el modulo
    `statistics` de la libreria estandar (mean/stdev con ddof=1 por
    definicion), independiente de numpy."""
    pnls = _pnls_for(shape_name)
    m = _ea_metrics_for(shape_name)

    arr = np.array(pnls, dtype=float)
    mean_r, std_r = float(np.mean(arr)), float(np.std(arr, ddof=1))
    guard_triggers = std_r <= abs(mean_r) * metrics.MIN_COEFFICIENT_OF_VARIATION

    if guard_triggers:
        assert m["sqn"] is None
        return

    oracle = oracle_sqn_stdlib(pnls)
    assert m["sqn"] is not None
    assert abs(m["sqn"] - oracle) <= TOL_SQN


def test_sqn_two_trades_below_label_threshold_but_value_present():
    """Con solo 2 trades (< MIN_TRADES_FOR_SQN_LABEL=20) el valor numerico
    se reporta igual (con nota "(orientativo)") pero el label queda en
    "N/A" -- verificado contra la API publica, y el valor numerico contra el
    oraculo stdlib."""
    pnls = _pnls_for("two_trades")
    m = _ea_metrics_for("two_trades")
    oracle = oracle_sqn_stdlib(pnls)
    assert m["sqn"] is not None
    assert abs(m["sqn"] - oracle) <= TOL_SQN
    assert m["sqn_label"] == "N/A"
    assert m["sqn_note"] == "(orientativo)"


@pytest.mark.xfail(
    strict=True,
    reason=(
        "DIVERGENCIA: metrics._calc_sqn usa sqrt(n)*mean/std SIN el cap "
        "sqrt(100) de la convencion capada de la comunidad, cuya atribucion "
        "a Van Tharp no esta verificada (docs/known-issues.md §7, "
        "docs/research/prior-art.md §2.2). Sobre una muestra FIJA de 150 "
        "trades, SQN sin cap != SQN con cap sqrt(min(n,100)) por > 0.3 -- "
        "ver oracle_sqn_tharp_capped vs oracle_sqn_stdlib en este mismo "
        "archivo. El repo Y su propia docstring coinciden en NO tener el "
        "cap (documentado, no un bug); este xfail deja visible cuanto se "
        "aleja de esa convencion capada para esta muestra de 150, sin "
        "asegurar nada sobre el crecimiento con N."
    ),
)
def test_sqn_uncapped_diverges_from_tharp_standard_for_large_n():
    import random

    rng = random.Random(20260716)
    pnls = [rng.uniform(-100.0, 150.0) for _ in range(150)]

    trades = make_trades(pnls)
    m = metrics.calculate_ea_metrics("MyEA", trades, make_config(capital=CAPITAL))

    tharp_capped = oracle_sqn_tharp_capped(pnls)
    assert m["sqn"] is not None
    # Esta asercion es la que FALLA a proposito (xfail strict): el repo
    # (sqrt(n), sin cap) no coincide con la convencion capada de la comunidad
    # (sqrt(min(n,100))), cuya atribucion a Tharp no esta verificada.
    assert abs(m["sqn"] - tharp_capped) <= TOL_SQN


# ─────────────────────────────────────────────────────────────────────────
# BINOMIAL P-VALUE (incubation_validator._binomial_p_value)
# ─────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "wins,n,p",
    [
        (3, 10, 0.5),
        (28, 50, 0.55),
        (2, 5, 0.5),
        (0, 20, 0.3),
        (20, 20, 0.9),
        (0, 0, 0.5),  # n<=0 -> 1.0 por contrato explicito del codigo
        (75, 100, 0.6),
    ],
)
def test_binomial_p_value_matches_scipy_exact_cdf(wins, n, p):
    """incubation_validator._binomial_p_value vs. scipy.stats.binom.cdf:
    oraculo EXACTO (misma definicion de CDF binomial acumulada), libreria
    distinta (scipy en vez de math.comb manual)."""
    code_val = incubation_validator._binomial_p_value(wins, n, p)
    oracle_val = oracle_binomial_p_value_scipy(wins, n, p)
    assert abs(code_val - oracle_val) <= TOL_BINOMIAL_P


@pytest.mark.parametrize("wins,n,p", [(3, 10, 0.5), (28, 50, 0.55), (2, 5, 0.5)])
def test_binomial_docs_normal_approximation_does_not_match_code(wins, n, p):
    """HALLAZGO D1: docs/metrics-formulas.md:386-391
    afirma que, sin scipy, el codigo cae a una aproximacion normal
    (z-score + erf). Eso es FALSO: incubation_validator.py:252-268 es puro
    math.comb, exacto, sin scipy y sin fallback normal (su propio docstring
    lo dice explicitamente). Este test prueba la NO-coincidencia entre lo
    que el doc describe y lo que el codigo hace: la divergencia debe ser
    grande (> 0.02) para casos de N pequeno, donde la aproximacion normal es
    mas floja.
    """
    code_val = incubation_validator._binomial_p_value(wins, n, p)
    doc_val = doc_binomial_normal_approximation(wins, n, p)
    scipy_exact = oracle_binomial_p_value_scipy(wins, n, p)

    # El codigo SI coincide con el CDF exacto (scipy) ...
    assert abs(code_val - scipy_exact) <= TOL_BINOMIAL_P
    # ... pero NO con la aproximacion normal que el doc afirma usar cuando
    # scipy no esta disponible. El doc describe un camino de codigo que no
    # existe.
    assert abs(code_val - doc_val) > 0.02
