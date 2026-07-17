"""
test_parser.py - Tests unitarios para parser.py.

No se usa ningún archivo .xlsx real. Se testean las funciones puras
exportadas directamente desde parser.py.
"""

import pytest
from datetime import datetime, date
import openpyxl
from parser import (
    SYSTEM_COMMENT_PREFIX,
    _is_system_comment,
    _to_float,
    _to_price_or_none,
    _parse_date,
    _find_section_rows,
    _parse_orders,
    _parse_positions,
    _parse_open_positions,
    merge_trades,
)


def _build_ws(rows):
    """Helper: build an in-memory worksheet from a list of row tuples
    (1-indexed rows are implicit: rows[0] -> row 1, rows[1] -> row 2, ...).
    """
    wb = openpyxl.Workbook()
    ws = wb.active
    for row_idx, row in enumerate(rows, start=1):
        for col_idx, val in enumerate(row, start=1):
            ws.cell(row=row_idx, column=col_idx, value=val)
    return ws


# ── Test 1: _parse_orders filtra comentarios de sistema ──────────────────────

def test_system_comment_prefix_constant():
    """
    SYSTEM_COMMENT_PREFIX es '['. Comentarios MT5 como '[sl 1.23]'
    deben ser identificados por este prefijo y excluidos del order_map.
    """
    assert SYSTEM_COMMENT_PREFIX == "["
    assert "[sl 1.09250]".startswith(SYSTEM_COMMENT_PREFIX)
    assert "[tp 2700.0]".startswith(SYSTEM_COMMENT_PREFIX)
    assert "MyEA".startswith(SYSTEM_COMMENT_PREFIX) is False
    assert "".startswith(SYSTEM_COMMENT_PREFIX) is False


def test_is_system_comment_precise_matcher():
    """
    _is_system_comment() usa un regex anclado (^\\[(sl|tp|so|out|expiration)\\b)
    en lugar del prefijo naive '[', para no descartar nombres de EA legítimos
    que también empiezan por '[' (ej. "[GridMaster v2]").
    """
    # Comentarios de sistema reales de MT5 → deben ser filtrados
    assert _is_system_comment("[sl 1.09250]") is True
    assert _is_system_comment("[tp 2700.0]") is True
    assert _is_system_comment("[so: partial]") is True
    assert _is_system_comment("[out 1.2345]") is True
    assert _is_system_comment("[expiration]") is True
    assert _is_system_comment("[SL 1.2345]") is True   # case-insensitive
    assert _is_system_comment("[tp]") is True
    assert _is_system_comment("[sl]") is True

    # Nombres de EA legítimos que empiezan por '[' → NO deben ser filtrados
    assert _is_system_comment("[GridMaster v2]") is False
    assert _is_system_comment("[Ichimoku_Bot]") is False
    assert _is_system_comment("[GRID] Recovery v3") is False
    assert _is_system_comment("MyEA") is False
    assert _is_system_comment("") is False


# ── Test 2: _to_float maneja strings, None, comas y formatos monetarios ──────

def test_to_float_conversions():
    """
    _to_float reemplaza coma por punto (formato europeo: "1,5" → 1.5).
    Cuando SOLO hay coma, se interpreta como separador de miles si sigue un
    patrón estricto de agrupación (ej. "1,000" → 1000.0); si no, como coma
    decimal (ej. "1,5" → 1.5).
    """
    assert _to_float(100.0) == 100.0
    assert _to_float("99.5") == 99.5
    assert _to_float("1,5") == 1.5       # formato europeo: coma decimal → punto
    assert _to_float("1,234.5") == 1234.5  # coma de miles + punto decimal → 1234.5
    assert _to_float(None) == 0.0        # None → default 0.0
    assert _to_float(None, default=-1.0) == -1.0
    assert _to_float("invalid") == 0.0


def test_to_float_thousands_and_currency_edge_cases():
    """
    Cobertura de todos los formatos reproducidos como defectos C1+C2:
    separadores de miles ambiguos, negativos entre paréntesis, símbolos
    de moneda, y separadores europeos invertidos (punto de miles, coma decimal).
    """
    # int/float/Decimal directo → sin conversión de string
    assert _to_float(1000) == 1000.0
    from decimal import Decimal
    assert _to_float(Decimal("12.34")) == 12.34

    # Solo coma, patrón estricto de miles → separador de miles
    assert _to_float("1,000") == 1000.0
    assert _to_float("1,234,567") == 1234567.0

    # Solo coma, NO patrón de miles → coma decimal (pin existente preservado)
    assert _to_float("1,5") == 1.5

    # Ambos ',' y '.': el ÚLTIMO que aparece es el separador decimal
    assert _to_float("1,234.56") == 1234.56   # '.' es el último → decimal
    assert _to_float("1.234,56") == 1234.56   # ',' es el último → decimal

    # Negativo entre paréntesis
    assert _to_float("(12.34)") == -12.34

    # Símbolos de moneda
    assert _to_float("$12.34") == 12.34
    assert _to_float("€12.34") == 12.34
    assert _to_float("USD 12.34") == 12.34
    assert _to_float("12.34 EUR") == 12.34

    # Espacio / NBSP como separador de miles
    assert _to_float("1 234.56") == 1234.56    # espacio normal
    assert _to_float("1 234.56") == 1234.56    # NBSP
    assert _to_float("1 234.56") == 1234.56    # narrow NBSP

    # Solo espacios → default
    assert _to_float("   ") == 0.0


# ── R1: _to_float — ambigüedad de coma "X,YYY" resuelta por contexto ─────────

def test_to_float_leading_zero_group_always_decimal_comma():
    """
    Regresión R1(a): un grupo inicial "0" (o "-0") antes de la coma NUNCA
    es agrupación de miles real (ningún número se agrupa como "0,NNN").
    Debe interpretarse SIEMPRE como coma decimal, en AMBOS modos —
    reproduce el bug crítico: "0,001" (volumen EU) ya NO debe convertirse
    en 1.0 (error de tamaño de posición x1000).
    """
    assert _to_float("0,001") == pytest.approx(0.001)
    assert _to_float("0,001", ambiguous_comma="decimal") == pytest.approx(0.001)
    assert _to_float("0,001", ambiguous_comma="thousands") == pytest.approx(0.001)

    assert _to_float("0,500") == pytest.approx(0.5)
    assert _to_float("0,500", ambiguous_comma="decimal") == pytest.approx(0.5)
    assert _to_float("0,500", ambiguous_comma="thousands") == pytest.approx(0.5)

    assert _to_float("-0,001", ambiguous_comma="thousands") == pytest.approx(-0.001)


def test_to_float_leading_zero_group_holes_f3():
    """
    F3 (JD-3 round-3): rule (a) must match an all-zeros leading group via
    regex (^-?0+$), not just the literal "0"/"-0" tuple, AND must only
    claim the single-comma-group case — a multi-group value still routes
    to the unambiguous multi-group thousands branch even when its leading
    group happens to be all zeros.

    - "00,001": a zero-padded leading zero group ("00") used to slip past
      the literal ("0", "-0") check into the ambiguous branch, where
      thousands mode wrongly turned it into 1.0 instead of 0.001.
    - "0,001,000": the leading "0" group used to match rule (a) FIRST
      (before the multi-group check), turning it into the invalid
      "0.001.000" string, which fails float() and silently falls back to
      the default (0.0) instead of the correct unambiguous thousands
      value.
    """
    assert _to_float("00,001") == pytest.approx(0.001)
    assert _to_float("00,001", ambiguous_comma="decimal") == pytest.approx(0.001)
    assert _to_float("00,001", ambiguous_comma="thousands") == pytest.approx(0.001)

    assert _to_float("0,001,000") == pytest.approx(1000.0)
    assert _to_float("0,001,000", ambiguous_comma="decimal") == pytest.approx(1000.0)
    assert _to_float("0,001,000", ambiguous_comma="thousands") == pytest.approx(1000.0)


def test_to_float_ambiguous_comma_resolved_by_caller_context():
    """
    Regresión R1(b): un grupo NO-cero de 1-3 dígitos + exactamente 3
    decimales ("1,234" / "145,678") es genuinamente ambiguo desde el
    string solo — la resolución depende del contexto del caller
    (columna MONEY vs PRICE/QUANTITY), vía el kwarg ambiguous_comma.
    """
    # PRICE (EU-locale USDJPY): "145,678" → 145.678, NO 145678.0
    assert _to_float("145,678", ambiguous_comma="decimal") == pytest.approx(145.678)
    assert _to_float("154,325", ambiguous_comma="decimal") == pytest.approx(154.325)

    # MONEY (thousands, default): mismo shape → 1000s grouping
    assert _to_float("145,678", ambiguous_comma="thousands") == pytest.approx(145678.0)
    assert _to_float("1,000") == pytest.approx(1000.0)  # default = thousands
    assert _to_float("1,000", ambiguous_comma="thousands") == pytest.approx(1000.0)

    # No es un grupo de 3 dígitos → decimal en AMBOS modos (no ambiguo)
    assert _to_float("1,5", ambiguous_comma="thousands") == pytest.approx(1.5)
    assert _to_float("1,5", ambiguous_comma="decimal") == pytest.approx(1.5)

    # Ambos separadores presentes → el ÚLTIMO decide, sin importar el modo
    assert _to_float("1,234.56", ambiguous_comma="decimal") == pytest.approx(1234.56)
    assert _to_float("1.234,56", ambiguous_comma="thousands") == pytest.approx(1234.56)


def test_to_float_rejects_non_finite_results():
    """
    R2: "nan"/"inf"/"-inf"/"Infinity" no son valores parseables válidos
    — el docstring promete "unparseable input returns default", así que
    deben rechazarse con math.isfinite() en vez de colarse como float('nan')
    (que rompe silenciosamente win/loss counts: nan no es ni >0 ni <0).
    Aplica también al passthrough nativo (float('nan') puede llegar
    directo desde openpyxl como float real, sin pasar por string).
    """
    assert _to_float("nan") == 0.0
    assert _to_float("inf") == 0.0
    assert _to_float("-inf") == 0.0
    assert _to_float("Infinity") == 0.0
    assert _to_float("nan", default=-1.0) == -1.0

    # Passthrough nativo: float('nan') ya como objeto float, sin string
    assert _to_float(float("nan")) == 0.0
    assert _to_float(float("inf")) == 0.0
    assert _to_float(float("-inf"), default=-1.0) == -1.0


def test_to_float_rejects_non_finite_default_f4():
    """
    F4 (JD-3 round-3): the docstring promises "non-finite is never
    returned as a value" — but math.isfinite() only ever validated the
    computed `result`, never the caller-supplied `default` itself. A
    non-finite `default` (e.g. float("nan")) must be normalized to 0.0
    at every `return default` path: None input, empty string, unparseable
    string, and non-finite computed result.
    """
    assert _to_float("garbage", default=float("nan")) == 0.0
    assert _to_float("garbage", default=float("inf")) == 0.0
    assert _to_float("garbage", default=float("-inf")) == 0.0
    assert _to_float(None, default=float("nan")) == 0.0
    assert _to_float("", default=float("nan")) == 0.0
    assert _to_float("nan", default=float("nan")) == 0.0
    # A finite default is untouched, unaffected by the new guard.
    assert _to_float("garbage", default=-1.0) == -1.0


def test_to_float_rejects_bool_passthrough():
    """
    R3: bool es subclase de int en Python, así que
    isinstance(val, (int, float, Decimal)) admite True/False. Una celda
    booleana de openpyxl no es un valor numérico real — debe tratarse
    como no-parseable → default, igual que el código antiguo.
    """
    assert _to_float(True) == 0.0
    assert _to_float(False) == 0.0
    assert _to_float(True, default=-1.0) == -1.0


def test_to_price_or_none_zero_is_unset_regardless_of_cell_type():
    """
    En MT5, S/L = 0 (o T/P = 0) significa "sin stop/target", no un precio
    de cero. El sentinel de ausencia llega en varias formas y TODAS deben
    dar None — antes el guard `if sl_val` sobre el valor crudo hacía que
    el 0.0 numérico (falsy) diera None pero el string "0" (truthy) parseara
    a 0.0 y se tratara como precio real. known-issues.md §10.
    """
    # Todas las representaciones de "sin stop" -> None
    assert _to_price_or_none(0.0) is None          # numérico (ya era correcto)
    assert _to_price_or_none(0) is None
    assert _to_price_or_none("0") is None           # el bug: antes daba 0.0
    assert _to_price_or_none("0.0") is None
    assert _to_price_or_none("0,0") is None          # coma decimal EU
    assert _to_price_or_none("0,00") is None
    assert _to_price_or_none("") is None
    assert _to_price_or_none("   ") is None
    assert _to_price_or_none(None) is None


def test_to_price_or_none_preserves_real_prices():
    """Un precio válido es estrictamente positivo y pasa sin cambios."""
    assert _to_price_or_none(1.23456) == pytest.approx(1.23456)
    assert _to_price_or_none("3000.5") == pytest.approx(3000.5)
    assert _to_price_or_none(1.2345) == pytest.approx(1.2345)
    # columna de precio -> coma ambigua se resuelve como decimal
    assert _to_price_or_none("1,2345") == pytest.approx(1.2345)


def test_to_price_or_none_rejects_nonpositive_and_garbage():
    """
    Un precio no puede ser <= 0. Un negativo (celda malformada, p. ej. un
    paréntesis de contabilidad) o basura no-parseable se trata como unset
    en vez de propagar un precio inválido — no adivinar es mejor que
    adivinar mal, la misma política que el resto de parser.py.
    """
    assert _to_price_or_none("(12.34)") is None     # negativo -> unset
    assert _to_price_or_none(-5.0) is None
    assert _to_price_or_none("invalid") is None
    assert _to_price_or_none("###") is None


def test_parse_positions_sl_zero_string_becomes_none_end_to_end():
    """
    Cierre end-to-end del bug: un export con S/L = "0" (string) debe dar
    sl=None, no sl=0.0. Fila con S/L string "0" y T/P con precio real, para
    probar los dos lados en una sola posición. known-issues.md §10.
    """
    ws = _build_ws([
        ["Time", "Position", "Symbol", "Type", "Volume", "Price", "S / L", "T / P",
         "Time", "Price", "Commission", "Swap", "Profit"],
        ["2026.01.02 10:00:00", 1001, "EURUSD", "buy", "0.10", "1.10000",
         "0", "1.15000", "2026.01.02 14:00:00", "1.10500", "0", "0", "50.0"],
    ])

    trades = _parse_positions(ws, header_row=1, data_start=2, end_row=2)

    assert len(trades) == 1
    t = trades[0]
    assert t["sl"] is None, f"S/L='0' string debe dar None, dio {t['sl']!r}"
    assert t["tp"] == pytest.approx(1.15), "un T/P real debe preservarse"


def test_parse_positions_eu_locale_comma_ambiguity_end_to_end():
    """
    R1 end-to-end: fila con formato EU-locale — volumen "0,010" (regla a,
    no ambiguo), precio "145,678" (regla b, ambiguo → PRICE = decimal) y
    profit "1,000" (regla b, ambiguo → MONEY = thousands). Prueba que
    _parse_positions aplica ambiguous_comma correctamente por columna.
    """
    ws = _build_ws([
        ["Time", "Position", "Symbol", "Type", "Volume", "Price", "S / L", "T / P",
         "Time", "Price", "Commission", "Swap", "Profit"],
        ["2026.01.02 10:00:00", 1001, "USDJPY", "buy", "0,010", "145,678",
         "0", "0", "2026.01.02 14:00:00", "145,678", "0", "0", "1,000"],
    ])

    trades = _parse_positions(ws, header_row=1, data_start=2, end_row=2)

    assert len(trades) == 1
    t = trades[0]
    assert t["volume"] == pytest.approx(0.01)
    assert t["open_price"] == pytest.approx(145.678)
    assert t["close_price"] == pytest.approx(145.678)
    assert t["profit"] == pytest.approx(1000.0)


# ── Test 3: _parse_date maneja múltiples formatos ────────────────────────────

def test_parse_date_formats():
    """_parse_date acepta datetime directo o string en formato MT5."""
    dt = datetime(2026, 1, 15, 10, 30, 0)

    # Datetime ya parseado → devuelve igual
    assert _parse_date(dt) == dt

    # Formato MT5 estándar
    assert _parse_date("2026.01.15 10:30:00") == dt

    # Formato ISO
    assert _parse_date("2026-01-15 10:30:00") == dt

    # None → None
    assert _parse_date(None) is None


def test_parse_date_additional_formats():
    """
    Cobertura de los formatos reproducidos como defectos en C7: sin
    segundos, con fracción de segundos, y celdas openpyxl de solo fecha
    (datetime.date, que NO es instancia de datetime).
    """
    # Sin segundos
    assert _parse_date("2026.01.15 10:30") == datetime(2026, 1, 15, 10, 30, 0)
    assert _parse_date("2026-01-15 10:30") == datetime(2026, 1, 15, 10, 30, 0)

    # Con fracción de segundos
    assert _parse_date("2026.01.15 10:30:00.123") == datetime(2026, 1, 15, 10, 30, 0, 123000)

    # Solo fecha (string)
    assert _parse_date("2026.01.15") == datetime(2026, 1, 15, 0, 0, 0)
    assert _parse_date("2026-01-15") == datetime(2026, 1, 15, 0, 0, 0)

    # Celda openpyxl de solo fecha (datetime.date, no es datetime)
    assert _parse_date(date(2024, 5, 1)) == datetime(2024, 5, 1, 0, 0, 0)

    # Fallback ISO genérico
    assert _parse_date("2026-01-15T10:30:00") == datetime(2026, 1, 15, 10, 30, 0)

    # Input genuinamente inválido → None
    assert _parse_date("not a date") is None
    assert _parse_date("") is None


# ── Tests 4-8: merge_trades() — función central del append mode ──────────────

def _make_trade(position_id, close_day, comment="MyEA", net_pnl=100.0):
    """Helper: trade mínimo para tests de merge."""
    return {
        "position_id": position_id,
        "comment": comment,
        "net_pnl": net_pnl,
        "close_time": datetime(2026, 1, close_day, 12, 0, 0),
        "direction": "buy",
    }


def test_merge_trades_deduplication():
    """
    Trades con el mismo position_id no se duplican.
    El trade NUEVO tiene precedencia sobre el existente (permite que
    correcciones del broker en un re-upload se reflejen en el cache).
    """
    existing = [_make_trade(1001, 2, net_pnl=99.0)]
    new_trades = [
        _make_trade(1001, 2, net_pnl=999.0),  # mismo ID, distinto pnl → el nuevo gana
        _make_trade(1002, 5, net_pnl=50.0),   # nuevo ID → debe agregarse
    ]

    result = merge_trades(existing, new_trades)

    assert len(result) == 2
    # El trade nuevo (net_pnl=999) tiene precedencia
    trade_1001 = next(t for t in result if t["position_id"] == 1001)
    assert trade_1001["net_pnl"] == pytest.approx(999.0)


def test_merge_trades_intra_batch_duplicate():
    """
    Duplicados DENTRO de un mismo upload (new_trades) no deben duplicarse
    en el resultado; la ÚLTIMA ocurrencia dentro de new_trades gana.
    Reproduce el bug de doble conteo: existing=[] con new_trades=[pos5001, pos5001]
    ya NO debe devolver 2 trades.
    """
    new_trades = [
        _make_trade(5001, 3, net_pnl=100.0),
        _make_trade(5001, 3, net_pnl=250.0),  # mismo ID repetido dentro del mismo batch → gana el último
    ]

    result = merge_trades([], new_trades)

    assert len(result) == 1
    assert result[0]["position_id"] == 5001
    assert result[0]["net_pnl"] == pytest.approx(250.0)


def test_merge_trades_sort_order():
    """
    El resultado siempre está ordenado por close_time ascendente.
    """
    existing = [_make_trade(1003, 10)]  # Jan 10
    new_trades = [
        _make_trade(1001, 2),   # Jan 2 — más antiguo
        _make_trade(1002, 5),   # Jan 5
    ]

    result = merge_trades(existing, new_trades)

    dates = [t["close_time"] for t in result]
    assert dates == sorted(dates)
    assert result[0]["position_id"] == 1001  # Jan 2 primero


def test_merge_trades_empty_existing():
    """merge_trades con existing vacío devuelve todos los new_trades."""
    new_trades = [_make_trade(1001, 2), _make_trade(1002, 5)]
    result = merge_trades([], new_trades)

    assert len(result) == 2
    assert {t["position_id"] for t in result} == {1001, 1002}


def test_merge_trades_empty_new():
    """merge_trades con new_trades vacío devuelve existing sin cambios."""
    existing = [_make_trade(1001, 2), _make_trade(1002, 5)]
    result = merge_trades(existing, [])

    assert len(result) == 2
    assert {t["position_id"] for t in result} == {1001, 1002}


def test_merge_trades_none_close_time_sorts_to_end():
    """
    Trades con close_time=None van al FINAL de la lista, no al inicio.
    Un None al inicio corrupiría la equity curve (que asume orden cronológico).
    """
    trade_with_none = {
        "position_id": 9999,
        "comment": "MyEA",
        "net_pnl": 0.0,
        "close_time": None,
        "direction": "buy",
    }
    existing = [_make_trade(1001, 2), _make_trade(1002, 5)]
    new_trades = [trade_with_none]

    result = merge_trades(existing, new_trades)

    # El trade sin close_time debe ir al final
    assert result[-1]["position_id"] == 9999
    assert result[0]["position_id"] == 1001


# ── W1: _find_section_rows conserva la PRIMERA ocurrencia ────────────────────

def test_find_section_rows_keeps_first_occurrence():
    """
    Si la palabra clave de una sección aparece más de una vez en la columna A
    (ej. dentro de una fila de datos), debe conservarse la PRIMERA ocurrencia
    (el header real), no la última.
    """
    ws = _build_ws([
        ["Positions"],           # row 1 — header real de la sección
        ["Time", "Position"],    # row 2 — column headers
        ["2026.01.01", 1001],    # row 3 — data
        ["Orders"],               # row 4 — siguiente sección
        ["Order"],                # row 5
        ["Positions"],             # row 6 — duplicado espurio, NO debe sobreescribir row 1
    ])

    sections = _find_section_rows(ws)

    assert sections["Positions"] == 1
    assert sections["Orders"] == 4


# ── W2: _parse_orders conserva el PRIMER comentario no-sistema por order_id ──

def test_parse_orders_keeps_first_comment_for_duplicate_order_id():
    """
    Si el mismo order_id aparece dos veces con comentarios distintos,
    debe conservarse el PRIMER comentario no-sistema encontrado, en vez de
    sobreescribirlo silenciosamente con el último.
    """
    ws = _build_ws([
        ["Order", "Comment"],   # row 1 — header
        [7001, "MyEA"],          # row 2 — primer comentario
        [7001, "OtherEA"],       # row 3 — mismo order_id, comentario distinto → debe ignorarse
        [7002, "[sl 1.2345]"],   # row 4 — comentario de sistema → excluido
    ])

    order_map = _parse_orders(ws, header_row=1, data_start=2, end_row=4)

    assert order_map[7001] == "MyEA"
    assert 7002 not in order_map


# ── C6: _parse_positions resuelve columnas duplicadas por orden de aparición ─

def test_parse_positions_standard_layout_unchanged():
    """El layout estándar de 13 columnas sigue funcionando igual que antes."""
    ws = _build_ws([
        ["Time", "Position", "Symbol", "Type", "Volume", "Price", "S / L", "T / P",
         "Time", "Price", "Commission", "Swap", "Profit"],
        [datetime(2026, 1, 2, 10, 0, 0), 1001, "EURUSD", "buy", 0.1, 1.0850,
         1.0820, 1.0900, datetime(2026, 1, 2, 14, 0, 0), 1.0875, -1.0, 0.0, 100.0],
    ])

    trades = _parse_positions(ws, header_row=1, data_start=2, end_row=2)

    assert len(trades) == 1
    t = trades[0]
    assert t["position_id"] == 1001
    assert t["open_time"] == datetime(2026, 1, 2, 10, 0, 0)
    assert t["close_time"] == datetime(2026, 1, 2, 14, 0, 0)
    assert t["open_price"] == pytest.approx(1.0850)
    assert t["close_price"] == pytest.approx(1.0875)
    assert t["net_pnl"] == pytest.approx(99.0)  # 100 - 1 + 0


def test_parse_positions_extra_column_resolved_by_occurrence_order():
    """
    Reproduce el bug C6: un header con una columna 'Deal' extra desplaza las
    posiciones fijas hardcodeadas (1, 6, 9, 10). La resolución por orden de
    aparición debe seguir encontrando Time/Price open y close correctamente.
    """
    ws = _build_ws([
        ["Time", "Position", "Symbol", "Type", "Volume", "Price", "S / L", "T / P",
         "Deal", "Time", "Price", "Commission", "Swap", "Profit"],
        [datetime(2026, 1, 2, 10, 0, 0), 1001, "EURUSD", "buy", 0.1, 1.0850,
         1.0820, 1.0900, 55555, datetime(2026, 1, 2, 14, 0, 0), 1.0875, -1.0, 0.0, 100.0],
    ])

    trades = _parse_positions(ws, header_row=1, data_start=2, end_row=2)

    assert len(trades) == 1
    t = trades[0]
    # Con el hardcode viejo (col 9/10), close_time habría sido None y
    # close_price 0.0 porque leería la columna "Deal"/segundo "Time".
    assert t["close_time"] == datetime(2026, 1, 2, 14, 0, 0)
    assert t["close_price"] == pytest.approx(1.0875)
    assert t["open_price"] == pytest.approx(1.0850)


# ── C-OPEN: _parse_open_positions — Commission ausente / Comment desplazado ──

def test_parse_open_positions_without_commission_column():
    """
    Layout estándar SIN columna 'Commission': commission debe ser 0.0
    (nunca un fallback posicional que caiga sobre Swap y lo duplique).
    """
    ws = _build_ws([
        ["Time", "Position", "Symbol", "Type", "Volume", "Price", "S / L", "T / P",
         "Price", "filler", "Swap", "Profit", "Comment"],
        [datetime(2026, 1, 2, 10, 0, 0), 5001, "EURUSD", "buy", 0.1, 1.0850,
         1.0800, 1.0900, 1.0860, None, -0.5, 150.75, "MyEA"],
    ])

    positions = _parse_open_positions(ws, header_row=1, data_start=2, end_row=2)

    assert len(positions) == 1
    p = positions[0]
    assert p["commission"] == 0.0
    assert p["swap"] == pytest.approx(-0.5)
    assert p["profit"] == pytest.approx(150.75)
    assert p["net_pnl"] == pytest.approx(150.25)  # 150.75 + 0.0 - 0.5 (NOT 149.75)
    assert p["comment"] == "MyEA"


def test_parse_open_positions_with_commission_column_comment_shifted():
    """
    Layout CON columna 'Commission': Comment se resuelve por header
    (col_map["Comment"]), no por posición fija 13 — de lo contrario el
    nombre del EA se convertía en el string del Profit ("150.75").
    """
    ws = _build_ws([
        ["Time", "Position", "Symbol", "Type", "Volume", "Price", "S / L", "T / P",
         "filler", "filler", "Commission", "Swap", "Profit", "Comment"],
        [datetime(2026, 1, 2, 10, 0, 0), 5002, "EURUSD", "buy", 0.1, 1.0850,
         1.0800, 1.0900, None, None, -2.0, -0.5, 150.75, "MyEA2"],
    ])

    positions = _parse_open_positions(ws, header_row=1, data_start=2, end_row=2)

    assert len(positions) == 1
    p = positions[0]
    assert p["commission"] == pytest.approx(-2.0)
    assert p["comment"] == "MyEA2"  # NOT "150.75"
    assert p["net_pnl"] == pytest.approx(148.25)  # 150.75 - 2.0 - 0.5


def test_parse_open_positions_comment_fallback_skips_numeric_candidates():
    """
    R5: cuando el header genuinamente carece de columna "Comment", el scan
    de fallback (cols 13-16) NO debe aceptar un valor numérico (int/float,
    o string que parsea como número) como nombre de EA — eso reproduciría
    una versión más acotada del bug original "el nombre del EA se convirtió
    en 150.75". Si ninguna celda del rango es texto genuino, el comentario
    debe quedar en "Unknown".
    """
    ws = _build_ws([
        ["Time", "Position", "Symbol", "Type", "Volume", "Price", "S / L", "T / P",
         "filler9", "Swap", "Profit", "filler12"],
        [datetime(2026, 1, 2, 10, 0, 0), 5004, "EURUSD", "buy", 0.1, 1.0850,
         1.0800, 1.0900, None, -0.5, 150.75, None,
         150.75, "0.5", None, None],  # cols 13-16: numeric candidates only
    ])

    positions = _parse_open_positions(ws, header_row=1, data_start=2, end_row=2)

    assert len(positions) == 1
    p = positions[0]
    assert p["comment"] == "Unknown"  # NOT "150.75" or "0.5"
    assert p["profit"] == pytest.approx(150.75)


def test_parse_open_positions_filters_system_comment_on_primary_read():
    """
    _is_system_comment() también se aplica a la lectura primaria desde
    col_map["Comment"], no solo al scan de fallback.
    """
    ws = _build_ws([
        ["Time", "Position", "Symbol", "Type", "Volume", "Price", "S / L", "T / P",
         "Price", "filler", "Swap", "Profit", "Comment"],
        [datetime(2026, 1, 2, 10, 0, 0), 5003, "EURUSD", "buy", 0.1, 1.0850,
         1.0800, 1.0900, 1.0860, None, 0.0, 20.0, "[sl 1.2345]"],
    ])

    positions = _parse_open_positions(ws, header_row=1, data_start=2, end_row=2)

    assert len(positions) == 1
    assert positions[0]["comment"] == "Unknown"
