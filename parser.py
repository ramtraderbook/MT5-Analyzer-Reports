"""
parser.py - MT5 xlsx trade history parser for EA Analyzer
Detects sections dynamically, joins POSITIONS with ORDERS to get EA comments.
"""

import openpyxl
from datetime import datetime


SECTION_KEYWORDS = {"Positions", "Orders", "Deals", "Open Positions", "Results"}

# MT5 auto-generates comments starting with '[' for SL/TP/SO triggers (e.g. "[sl 1234.5]").
# Any non-empty comment that does NOT start with '[' is treated as a valid EA name.
SYSTEM_COMMENT_PREFIX = "["


def _parse_date(val):
    """Parse MT5 date string or datetime object to datetime."""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val
    s = str(val).strip()
    try:
        return datetime.strptime(s, "%Y.%m.%d %H:%M:%S")
    except ValueError:
        try:
            return datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None


def _to_float(val, default=0.0):
    """Safely convert value to float."""
    if val is None:
        return default
    try:
        return float(str(val).replace(",", ".").strip())
    except (ValueError, TypeError):
        return default


def _find_section_rows(ws):
    """
    Scan column A for section header keywords.
    Returns dict: { "Positions": row_idx, "Orders": row_idx, ... }
    """
    sections = {}
    for row_idx in range(1, ws.max_row + 1):
        cell_val = ws.cell(row=row_idx, column=1).value
        if isinstance(cell_val, str) and cell_val.strip() in SECTION_KEYWORDS:
            sections[cell_val.strip()] = row_idx
    return sections


def _compute_boundaries(section_starts, max_row):
    """
    Returns list of (name, header_row, data_start_row, end_row) tuples.
    header_row = section_row + 1 (column headers)
    data_start_row = header_row + 1 (first data row)
    end_row = next section start - 1
    """
    sorted_sections = sorted(section_starts.items(), key=lambda x: x[1])
    boundaries = []
    for i, (name, section_row) in enumerate(sorted_sections):
        header_row = section_row + 1
        data_start = section_row + 2
        if i + 1 < len(sorted_sections):
            end_row = sorted_sections[i + 1][1] - 1
        else:
            end_row = max_row
        boundaries.append((name, header_row, data_start, end_row))
    return boundaries


def _get_column_map(ws, header_row):
    """Read header row and return {column_name: column_index (1-based)}."""
    col_map = {}
    for col in range(1, 30):
        val = ws.cell(row=header_row, column=col).value
        if val is not None:
            col_map[str(val).strip()] = col
    return col_map


def _parse_header(ws):
    """Extract account info from the first 5 rows."""
    account = {
        "name": "",
        "number": "",
        "currency": "USD",
        "broker": "",
        "report_date": ""
    }
    for row_idx in range(1, 6):
        val = ws.cell(row=row_idx, column=1).value
        if not val:
            continue
        s = str(val)
        if s.startswith("Name:"):
            account["name"] = s.replace("Name:", "").strip()
        elif s.startswith("Account:"):
            parts = s.replace("Account:", "").strip()
            # Extract account number (first token)
            tokens = parts.split()
            if tokens:
                account["number"] = tokens[0].strip("()")
            # Extract currency
            for t in tokens:
                if t in ("USD", "EUR", "GBP"):
                    account["currency"] = t
                    break
        elif s.startswith("Company:"):
            account["broker"] = s.replace("Company:", "").strip()
        elif s.startswith("Date:"):
            account["report_date"] = s.replace("Date:", "").strip()
    return account


def _parse_positions(ws, header_row, data_start, end_row):
    """
    Parse POSITIONS section. Returns list of trade dicts.
    Columns: Time(open), Position, Symbol, Type, Volume, Price(open),
             S/L, T/P, Time(close), Price(close), Commission, Swap, Profit

    NOTE: "Time" and "Price" appear TWICE in headers (open + close).
    _get_column_map only stores the LAST occurrence, so we use positional
    fallbacks for these duplicate columns instead of the col_map.
    """
    col_map = _get_column_map(ws, header_row)

    # For unique column names, use col_map with positional fallback
    def get_col(name, fallback):
        return col_map.get(name, fallback)

    # Duplicate header names (Time, Price appear twice) → use fixed positions
    time_open_col   = 1   # Always first column in POSITIONS
    price_open_col  = 6   # Always 6th column (first "Price")
    time_close_col  = 9   # Always 9th column (second "Time")
    price_close_col = 10  # Always 10th column (second "Price")

    # Unique column names — safe to use col_map
    position_col   = get_col("Position", 2)
    symbol_col     = get_col("Symbol", 3)
    type_col       = get_col("Type", 4)
    volume_col     = get_col("Volume", 5)
    sl_col         = col_map.get("S / L", col_map.get("S/L", 7))
    tp_col         = col_map.get("T / P", col_map.get("T/P", 8))
    commission_col = get_col("Commission", 11)
    swap_col       = get_col("Swap", 12)
    profit_col     = get_col("Profit", 13)

    trades = []
    for row_idx in range(data_start, end_row + 1):
        pos_id_val = ws.cell(row=row_idx, column=position_col).value
        if pos_id_val is None:
            continue

        # Skip rows that are subtotals or headers (non-numeric position ID)
        try:
            pos_id = int(pos_id_val)
        except (ValueError, TypeError):
            continue

        open_time = _parse_date(ws.cell(row=row_idx, column=time_open_col).value)
        close_time = _parse_date(ws.cell(row=row_idx, column=time_close_col).value)
        commission = _to_float(ws.cell(row=row_idx, column=commission_col).value)
        swap = _to_float(ws.cell(row=row_idx, column=swap_col).value)
        profit = _to_float(ws.cell(row=row_idx, column=profit_col).value)
        net_pnl = profit + commission + swap

        sl_val = ws.cell(row=row_idx, column=sl_col).value
        tp_val = ws.cell(row=row_idx, column=tp_col).value

        duration_hours = 0.0
        if open_time and close_time:
            delta = close_time - open_time
            duration_hours = delta.total_seconds() / 3600

        trades.append({
            "position_id": pos_id,
            "symbol": str(ws.cell(row=row_idx, column=symbol_col).value or "").strip(),
            "direction": str(ws.cell(row=row_idx, column=type_col).value or "").strip().lower(),
            "volume": _to_float(ws.cell(row=row_idx, column=volume_col).value),
            "open_time": open_time,
            "close_time": close_time,
            "open_price": _to_float(ws.cell(row=row_idx, column=price_open_col).value),
            "close_price": _to_float(ws.cell(row=row_idx, column=price_close_col).value),
            "sl": _to_float(sl_val) if sl_val else None,
            "tp": _to_float(tp_val) if tp_val else None,
            "commission": commission,
            "swap": swap,
            "profit": profit,
            "net_pnl": net_pnl,
            "duration_hours": round(duration_hours, 2),
            "comment": "Unknown"  # will be filled by JOIN with ORDERS
        })

    return trades


def _parse_orders(ws, header_row, data_start, end_row):
    """
    Parse ORDERS section. Returns {order_id (int): comment (str)}.
    Only keeps rows with comments starting with valid EA prefixes.
    """
    col_map = _get_column_map(ws, header_row)

    order_col = col_map.get("Order", 2)
    comment_col = col_map.get("Comment", 12)

    order_map = {}
    for row_idx in range(data_start, end_row + 1):
        order_val = ws.cell(row=row_idx, column=order_col).value
        comment_val = ws.cell(row=row_idx, column=comment_col).value

        if order_val is None:
            continue

        try:
            order_id = int(order_val)
        except (ValueError, TypeError):
            continue

        comment_str = str(comment_val or "").strip()
        # Keep any non-empty comment that isn't an MT5 system comment (e.g. "[sl 1234.5]")
        if comment_str and not comment_str.startswith(SYSTEM_COMMENT_PREFIX):
            order_map[order_id] = comment_str

    return order_map


def _parse_open_positions(ws, header_row, data_start, end_row):
    """
    Parse OPEN POSITIONS section.
    Comment is in column M (13) directly.
    """
    col_map = _get_column_map(ws, header_row)

    time_col = col_map.get("Time", 1)
    position_col = col_map.get("Position", 2)
    symbol_col = col_map.get("Symbol", 3)
    type_col = col_map.get("Type", 4)
    volume_col = col_map.get("Volume", 5)
    price_open_col = col_map.get("Price", 6)
    sl_col = col_map.get("S / L", col_map.get("S/L", 7))
    tp_col = col_map.get("T / P", col_map.get("T/P", 8))
    commission_col = col_map.get("Commission", 11)
    swap_col = col_map.get("Swap", 12)
    profit_col = col_map.get("Profit", 13)
    comment_col = 13  # Comment is at col M for open positions

    open_positions = []
    for row_idx in range(data_start, end_row + 1):
        pos_id_val = ws.cell(row=row_idx, column=position_col).value
        if pos_id_val is None:
            continue
        try:
            pos_id = int(pos_id_val)
        except (ValueError, TypeError):
            continue

        commission = _to_float(ws.cell(row=row_idx, column=commission_col).value)
        swap = _to_float(ws.cell(row=row_idx, column=swap_col).value)
        profit = _to_float(ws.cell(row=row_idx, column=profit_col).value)

        # For open positions, the comment might be at a different col
        # Try col 13 first, then look for it in col_map
        comment_val = ws.cell(row=row_idx, column=comment_col).value
        if not comment_val or not str(comment_val).strip():
            # try next columns
            for c in range(13, 17):
                v = ws.cell(row=row_idx, column=c).value
                if v and str(v).strip() and not str(v).startswith(SYSTEM_COMMENT_PREFIX):
                    comment_val = v
                    break

        sl_val = ws.cell(row=row_idx, column=sl_col).value
        tp_val = ws.cell(row=row_idx, column=tp_col).value

        open_positions.append({
            "position_id": pos_id,
            "comment": str(comment_val or "Unknown").strip(),
            "symbol": str(ws.cell(row=row_idx, column=symbol_col).value or "").strip(),
            "direction": str(ws.cell(row=row_idx, column=type_col).value or "").strip().lower(),
            "volume": _to_float(ws.cell(row=row_idx, column=volume_col).value),
            "open_time": _parse_date(ws.cell(row=row_idx, column=time_col).value),
            "open_price": _to_float(ws.cell(row=row_idx, column=price_open_col).value),
            "sl": _to_float(sl_val) if sl_val else None,
            "tp": _to_float(tp_val) if tp_val else None,
            "commission": commission,
            "swap": swap,
            "profit": profit,
            "net_pnl": profit + commission + swap,
        })

    return open_positions


def _parse_results(ws, header_row, data_start, end_row):
    """
    Parse RESULTS section for validation.
    Returns dict of label -> value pairs.
    """
    results = {}
    for row_idx in range(header_row, end_row + 1):
        for col in range(1, 10):
            label = ws.cell(row=row_idx, column=col).value
            if label and isinstance(label, str) and label.strip():
                val = ws.cell(row=row_idx, column=col + 1).value
                results[label.strip()] = val
                break
    return results


def parse_mt5_report(filepath: str) -> dict:
    """
    Main entry point. Parses an MT5 xlsx trade history report.
    Returns ParsedData dict with account info, closed trades, open positions.

    Raises ValueError with descriptive message on parse failure.
    """
    try:
        wb = openpyxl.load_workbook(filepath, data_only=True, read_only=False)
    except Exception as e:
        raise ValueError(f"No se pudo abrir el archivo xlsx: {e}")

    ws = wb.active

    # Find all section boundaries
    section_starts = _find_section_rows(ws)

    required = ["Positions", "Orders"]
    for req in required:
        if req not in section_starts:
            raise ValueError(
                f"Sección '{req}' no encontrada en el archivo. "
                "Verifica que el archivo sea un reporte de MT5 válido."
            )

    boundaries = _compute_boundaries(section_starts, ws.max_row)
    bound_map = {name: (hr, ds, er) for name, hr, ds, er in boundaries}

    # Parse header (account info)
    account = _parse_header(ws)

    # Parse POSITIONS (closed trades without comments)
    pos_hr, pos_ds, pos_er = bound_map["Positions"]
    closed_trades = _parse_positions(ws, pos_hr, pos_ds, pos_er)

    # Parse ORDERS (get EA comment for each position)
    ord_hr, ord_ds, ord_er = bound_map["Orders"]
    order_map = _parse_orders(ws, ord_hr, ord_ds, ord_er)

    # JOIN: enrich each trade with EA comment
    unknown_count = 0
    for trade in closed_trades:
        pid = trade["position_id"]
        if pid in order_map:
            trade["comment"] = order_map[pid]
        else:
            trade["comment"] = "Unknown"
            unknown_count += 1

    # Parse OPEN POSITIONS (if present)
    open_positions = []
    if "Open Positions" in bound_map:
        op_hr, op_ds, op_er = bound_map["Open Positions"]
        open_positions = _parse_open_positions(ws, op_hr, op_ds, op_er)

    # Parse RESULTS (for validation)
    results_validation = {}
    if "Results" in bound_map:
        res_hr, res_ds, res_er = bound_map["Results"]
        results_validation = _parse_results(ws, res_hr, res_ds, res_er)

    wb.close()

    # Build sorted list of unique EA names (exclude Unknown)
    ea_names = sorted(set(
        t["comment"] for t in closed_trades
        if t["comment"] != "Unknown"
    ))

    return {
        "account": account,
        "closed_trades": closed_trades,
        "open_positions": open_positions,
        "ea_names": ea_names,
        "results_validation": results_validation,
        "unknown_trades": unknown_count,
        "total_closed": len(closed_trades),
        "total_open": len(open_positions),
    }
