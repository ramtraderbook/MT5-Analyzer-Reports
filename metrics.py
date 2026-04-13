"""
metrics.py - Performance metrics calculation for EA Analyzer
All metrics computed from net P&L (Profit + Commission + Swap).
"""

import math
import numpy as np
from datetime import date, datetime, timedelta


TODAY = date.today()

EA_COLORS = [
    "#4FC3F7", "#FF7043", "#66BB6A", "#AB47BC",
    "#FFA726", "#26C6DA", "#EC407A", "#8D6E63",
    "#78909C", "#D4E157", "#5C6BC0", "#FF8A65"
]

SQN_LABELS = [
    (7.0, "Santo Grial"),
    (5.0, "Sobresaliente"),
    (3.0, "Excelente"),
    (2.5, "Bueno"),
    (2.0, "Promedio"),
    (1.6, "Debajo promedio"),
    (0.0, "Pobre"),
]


def _sqn_label(sqn_val):
    for threshold, label in sorted(SQN_LABELS, key=lambda x: -x[0]):
        if sqn_val >= threshold:
            return label
    return "Pobre"


def _build_equity_curve(trades_sorted):
    """
    Build equity curve showing NET P&L from 0 (not absolute equity).
    The Y axis represents gains/losses relative to starting capital.
    Positive = profit, negative = loss.
    Returns list of {"date": iso_str, "equity": float}.
    """
    if not trades_sorted:
        return []

    # Add initial point at 0 one day before first trade
    first_dt = trades_sorted[0]["close_time"]
    if isinstance(first_dt, str):
        first_dt = datetime.fromisoformat(first_dt)
    initial_date = (first_dt.date() - timedelta(days=1)).isoformat()

    pnl = 0.0
    curve = [{"date": initial_date, "equity": 0.0}]

    for trade in trades_sorted:
        pnl += trade["net_pnl"]
        close_dt = trade["close_time"]
        if isinstance(close_dt, str):
            close_dt = datetime.fromisoformat(close_dt)
        date_str = close_dt.date().isoformat() if close_dt else "unknown"
        curve.append({"date": date_str, "equity": round(pnl, 2)})

    return curve


def _build_drawdown_curve(equity_curve, capital):
    """
    Build drawdown curve from P&L equity curve (values relative to 0).
    DD% uses (capital + peak_pnl) as denominator.
    Returns list of {"date": iso_str, "dd_pct": float (negative)}.
    """
    if not equity_curve:
        return []

    peak_pnl = 0.0  # P&L peak starts at 0
    dd_curve = []

    for point in equity_curve:
        pnl = point["equity"]  # net P&L value
        if pnl > peak_pnl:
            peak_pnl = pnl
        dd_dollar = peak_pnl - pnl
        peak_abs = capital + peak_pnl
        dd_pct = -(dd_dollar / peak_abs * 100) if peak_abs > 0 else 0.0
        dd_curve.append({"date": point["date"], "dd_pct": round(dd_pct, 4)})

    return dd_curve


def _calc_max_drawdown(equity_curve, capital):
    """
    Returns (max_dd_dollar, max_dd_pct, last_peak_date_str).
    equity_curve values are net P&L (starting from 0).
    DD% uses (capital + peak_pnl) as denominator.
    last_peak_date_str is the date of the most recent P&L peak.
    """
    if not equity_curve:
        return 0.0, 0.0, None

    peak_pnl = 0.0  # track P&L peak (starts at 0)
    max_dd_dollar = 0.0
    max_dd_pct = 0.0
    last_peak_date = equity_curve[0]["date"]

    for point in equity_curve:
        pnl = point["equity"]  # net P&L
        if pnl >= peak_pnl:
            peak_pnl = pnl
            last_peak_date = point["date"]

        dd_dollar = peak_pnl - pnl
        if dd_dollar > max_dd_dollar:
            max_dd_dollar = dd_dollar
            peak_abs = capital + peak_pnl
            max_dd_pct = (dd_dollar / peak_abs * 100) if peak_abs > 0 else 0.0

    return round(max_dd_dollar, 2), round(max_dd_pct, 4), last_peak_date


def _calc_stagnation(last_peak_date_str):
    """Days from last equity peak to today."""
    if not last_peak_date_str:
        return 0
    try:
        last_peak = date.fromisoformat(last_peak_date_str)
        return max(0, (TODAY - last_peak).days)
    except (ValueError, TypeError):
        return 0


def _calc_sharpe(net_pnl_list):
    """Simplified per-trade Sharpe = mean(R) / std(R, ddof=1). No risk-free rate."""
    n = len(net_pnl_list)
    if n < 2:
        return None
    arr = np.array(net_pnl_list, dtype=float)
    std_r = float(np.std(arr, ddof=1))
    if std_r == 0:
        return None
    return round(float(np.mean(arr)) / std_r, 2)


def _calc_sqn(net_pnl_list):
    """
    SQN = sqrt(N) * mean(R) / std(R, ddof=1)
    Returns (sqn_val or None, note_str, label_str)
    """
    n = len(net_pnl_list)
    if n < 2:
        return None, "(insuficientes datos)", "N/A"

    arr = np.array(net_pnl_list, dtype=float)
    mean_r = float(np.mean(arr))
    std_r = float(np.std(arr, ddof=1))

    if std_r == 0:
        return None, "(desviación cero)", "N/A"

    sqn = math.sqrt(n) * mean_r / std_r
    note = "(orientativo)" if n < 20 else ""
    label = _sqn_label(sqn)

    return round(sqn, 2), note, label


def _calc_streaks(net_pnl_list):
    """
    Returns (max_wins, max_losses, avg_wins, avg_losses).
    """
    max_wins = max_losses = 0
    curr_win = curr_loss = 0
    win_streaks = []
    loss_streaks = []

    for pnl in net_pnl_list:
        if pnl > 0:
            curr_win += 1
            if curr_loss > 0:
                loss_streaks.append(curr_loss)
                curr_loss = 0
            max_wins = max(max_wins, curr_win)
        else:
            curr_loss += 1
            if curr_win > 0:
                win_streaks.append(curr_win)
                curr_win = 0
            max_losses = max(max_losses, curr_loss)

    # Flush remaining
    if curr_win > 0:
        win_streaks.append(curr_win)
    if curr_loss > 0:
        loss_streaks.append(curr_loss)

    avg_wins = round(sum(win_streaks) / len(win_streaks), 1) if win_streaks else 0.0
    avg_losses = round(sum(loss_streaks) / len(loss_streaks), 1) if loss_streaks else 0.0

    return max_wins, max_losses, avg_wins, avg_losses


def _weeks_operating(trades_sorted):
    """Weeks from first to last trade close_time."""
    if len(trades_sorted) < 2:
        return 0.0

    def to_dt(t):
        ct = t["close_time"]
        if isinstance(ct, str):
            return datetime.fromisoformat(ct)
        return ct

    first = to_dt(trades_sorted[0])
    last = to_dt(trades_sorted[-1])
    if first and last:
        delta = last - first
        return round(delta.total_seconds() / (7 * 86400), 1)
    return 0.0


def calculate_ea_metrics(ea_name: str, trades: list, config: dict) -> dict:
    """
    Calculate all metrics for a single EA.
    trades: list of trade dicts for this EA only.
    config: full config dict.
    Returns MetricsResult dict.
    """
    if not trades:
        return _empty_metrics(ea_name, config)

    # Sort by close_time
    def sort_key(t):
        ct = t["close_time"]
        if isinstance(ct, str):
            return datetime.fromisoformat(ct)
        return ct or datetime.min

    trades_sorted = sorted(trades, key=sort_key)

    net_pnl_list = [t["net_pnl"] for t in trades_sorted]

    # P&L breakdown
    wins = [p for p in net_pnl_list if p > 0]
    losses = [p for p in net_pnl_list if p <= 0]

    gross_profit = sum(wins)
    gross_loss = sum(losses)
    net_profit = gross_profit + gross_loss

    total_trades = len(net_pnl_list)
    winning_trades = len(wins)
    losing_trades = len(losses)

    win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0.0
    avg_win = (gross_profit / winning_trades) if winning_trades > 0 else 0.0
    avg_loss = (gross_loss / losing_trades) if losing_trades > 0 else 0.0

    profit_factor = (gross_profit / abs(gross_loss)) if gross_loss != 0 else (float("inf") if gross_profit > 0 else 0.0)
    payout_ratio = (avg_win / abs(avg_loss)) if avg_loss != 0 else (float("inf") if avg_win > 0 else 0.0)
    expectancy = net_profit / total_trades if total_trades > 0 else 0.0

    best_trade = max(net_pnl_list) if net_pnl_list else 0.0
    worst_trade = min(net_pnl_list) if net_pnl_list else 0.0

    # Long/short breakdown
    long_trades = [t for t in trades_sorted if t["direction"] == "buy"]
    short_trades = [t for t in trades_sorted if t["direction"] == "sell"]
    long_wins = sum(1 for t in long_trades if t["net_pnl"] > 0)
    short_wins = sum(1 for t in short_trades if t["net_pnl"] > 0)

    # Avg duration
    durations = [t["duration_hours"] for t in trades_sorted if t.get("duration_hours") is not None]
    avg_duration = round(sum(durations) / len(durations), 2) if durations else 0.0

    # Capital from config (default $5,000)
    mapping = config.get("mappings", {}).get(ea_name, {})
    capital = float(mapping.get("capital", 5000.0))

    # Equity & drawdown curves
    equity_curve = _build_equity_curve(trades_sorted)
    dd_curve = _build_drawdown_curve(equity_curve, capital)

    max_dd_dollar, max_dd_pct, last_peak_date = _calc_max_drawdown(equity_curve, capital)
    stagnation_days = _calc_stagnation(last_peak_date)

    ret_dd = (net_profit / max_dd_dollar) if max_dd_dollar > 0 else None
    recovery_factor = (net_profit / max_dd_dollar) if max_dd_dollar > 0 else None

    sqn_val, sqn_note, sqn_label = _calc_sqn(net_pnl_list)
    sharpe = _calc_sharpe(net_pnl_list)
    max_wins, max_losses, avg_wins_streak, avg_losses_streak = _calc_streaks(net_pnl_list)
    weeks = _weeks_operating(trades_sorted)

    # Instrument from trades
    symbols = list(set(t["symbol"] for t in trades_sorted))
    instrument = symbols[0] if len(symbols) == 1 else ", ".join(sorted(symbols))
    magic = mapping.get("magic")
    alias = mapping.get("alias", "") or ea_name   # alias if set, else original name
    mapped_instrument = mapping.get("instrument", instrument)
    label = f"{magic} - {alias}" if magic else alias

    # Format helpers
    def fmt_pf(val):
        if val == float("inf") or val != val:
            return "∞"
        return round(val, 2)

    return {
        "ea_name": ea_name,
        "magic": magic,
        "label": label,
        "instrument": mapped_instrument or instrument,
        "total_trades": total_trades,
        "winning_trades": winning_trades,
        "losing_trades": losing_trades,
        "long_trades": len(long_trades),
        "short_trades": len(short_trades),
        "long_wins": long_wins,
        "short_wins": short_wins,
        "net_profit": round(net_profit, 2),
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        "best_trade": round(best_trade, 2),
        "worst_trade": round(worst_trade, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "win_rate": round(win_rate, 2),
        "profit_factor": fmt_pf(profit_factor),
        "payout_ratio": fmt_pf(payout_ratio),
        "expectancy": round(expectancy, 2),
        "max_dd_dollar": max_dd_dollar,
        "max_dd_pct": round(max_dd_pct, 2),
        "ret_dd": round(ret_dd, 2) if ret_dd is not None else None,
        "recovery_factor": round(recovery_factor, 2) if recovery_factor is not None else None,
        "sqn": sqn_val,
        "sqn_note": sqn_note,
        "sqn_label": sqn_label,
        "sharpe_ratio": sharpe,
        "weeks_operating": weeks,
        "avg_duration_hours": avg_duration,
        "stagnation_days": stagnation_days,
        "max_consec_wins": max_wins,
        "max_consec_losses": max_losses,
        "avg_consec_wins": avg_wins_streak,
        "avg_consec_losses": avg_losses_streak,
        "equity_curve": equity_curve,
        "drawdown_curve": dd_curve,
        "trades": trades_sorted,
    }


def calculate_portfolio_metrics(all_trades: list, config: dict = None) -> dict:
    """
    Calculate portfolio-wide metrics using all closed trades combined.
    Portfolio capital = sum of each EA's capital from config (default $5,000 each).
    """
    if config is None:
        config = {}
    if not all_trades:
        return _empty_metrics("PORTFOLIO", {})

    def sort_key(t):
        ct = t["close_time"]
        if isinstance(ct, str):
            return datetime.fromisoformat(ct)
        return ct or datetime.min

    trades_sorted = sorted(all_trades, key=sort_key)
    net_pnl_list = [t["net_pnl"] for t in trades_sorted]

    wins = [p for p in net_pnl_list if p > 0]
    losses = [p for p in net_pnl_list if p <= 0]

    gross_profit = sum(wins)
    gross_loss = sum(losses)
    net_profit = gross_profit + gross_loss

    total_trades = len(net_pnl_list)
    winning_trades = len(wins)
    losing_trades = len(losses)

    win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0.0
    avg_win = (gross_profit / winning_trades) if winning_trades > 0 else 0.0
    avg_loss = (gross_loss / losing_trades) if losing_trades > 0 else 0.0

    profit_factor = (gross_profit / abs(gross_loss)) if gross_loss != 0 else (float("inf") if gross_profit > 0 else 0.0)
    payout_ratio = (avg_win / abs(avg_loss)) if avg_loss != 0 else (float("inf") if avg_win > 0 else 0.0)
    expectancy = net_profit / total_trades if total_trades > 0 else 0.0

    best_trade = max(net_pnl_list) if net_pnl_list else 0.0
    worst_trade = min(net_pnl_list) if net_pnl_list else 0.0

    long_trades = [t for t in trades_sorted if t["direction"] == "buy"]
    short_trades = [t for t in trades_sorted if t["direction"] == "sell"]
    long_wins = sum(1 for t in long_trades if t["net_pnl"] > 0)
    short_wins = sum(1 for t in short_trades if t["net_pnl"] > 0)

    durations = [t["duration_hours"] for t in trades_sorted if t.get("duration_hours") is not None]
    avg_duration = round(sum(durations) / len(durations), 2) if durations else 0.0

    # Portfolio capital = sum of each EA's capital from config
    mappings = config.get("mappings", {})
    ea_names_in_portfolio = list(set(t["comment"] for t in all_trades if t.get("comment") and t["comment"] != "Unknown"))
    portfolio_capital = sum(float(mappings.get(ea, {}).get("capital", 5000.0)) for ea in ea_names_in_portfolio)
    if portfolio_capital <= 0:
        portfolio_capital = 5000.0  # fallback

    equity_curve = _build_equity_curve(trades_sorted)
    dd_curve = _build_drawdown_curve(equity_curve, portfolio_capital)

    max_dd_dollar, max_dd_pct, last_peak_date = _calc_max_drawdown(equity_curve, portfolio_capital)
    stagnation_days = _calc_stagnation(last_peak_date)

    ret_dd = (net_profit / max_dd_dollar) if max_dd_dollar > 0 else None
    recovery_factor = (net_profit / max_dd_dollar) if max_dd_dollar > 0 else None

    sqn_val, sqn_note, sqn_label = _calc_sqn(net_pnl_list)
    sharpe = _calc_sharpe(net_pnl_list)
    max_wins, max_losses, avg_wins_streak, avg_losses_streak = _calc_streaks(net_pnl_list)
    weeks = _weeks_operating(trades_sorted)

    def fmt_pf(val):
        if val == float("inf") or val != val:
            return "∞"
        return round(val, 2)

    return {
        "ea_name": "PORTFOLIO",
        "magic": None,
        "label": "PORTFOLIO",
        "instrument": "—",
        "total_trades": total_trades,
        "winning_trades": winning_trades,
        "losing_trades": losing_trades,
        "long_trades": len(long_trades),
        "short_trades": len(short_trades),
        "long_wins": long_wins,
        "short_wins": short_wins,
        "net_profit": round(net_profit, 2),
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        "best_trade": round(best_trade, 2),
        "worst_trade": round(worst_trade, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "win_rate": round(win_rate, 2),
        "profit_factor": fmt_pf(profit_factor),
        "payout_ratio": fmt_pf(payout_ratio),
        "expectancy": round(expectancy, 2),
        "max_dd_dollar": max_dd_dollar,
        "max_dd_pct": round(max_dd_pct, 2),
        "ret_dd": round(ret_dd, 2) if ret_dd is not None else None,
        "recovery_factor": round(recovery_factor, 2) if recovery_factor is not None else None,
        "sqn": sqn_val,
        "sqn_note": sqn_note,
        "sqn_label": sqn_label,
        "sharpe_ratio": sharpe,
        "weeks_operating": weeks,
        "avg_duration_hours": avg_duration,
        "stagnation_days": stagnation_days,
        "max_consec_wins": max_wins,
        "max_consec_losses": max_losses,
        "avg_consec_wins": avg_wins_streak,
        "avg_consec_losses": avg_losses_streak,
        "equity_curve": equity_curve,
        "drawdown_curve": dd_curve,
        "trades": trades_sorted,
    }


def calculate_all_metrics(parsed_data: dict, config: dict) -> dict:
    """
    Calculate metrics for all EAs and the portfolio.
    Returns {"portfolio": MetricsResult, "by_ea": {ea_name: MetricsResult}}
    Also returns "ea_colors" mapping ea_name -> hex color for charts.
    """
    closed_trades = parsed_data.get("closed_trades", [])
    all_ea_names = parsed_data.get("ea_names", [])

    # Filter to only EAs marked active in config (default True for unmapped EAs)
    mappings = config.get("mappings", {})
    ea_names = [ea for ea in all_ea_names if mappings.get(ea, {}).get("active", True)]

    by_ea = {}
    ea_colors = {}

    for i, ea_name in enumerate(ea_names):
        ea_trades = [t for t in closed_trades if t.get("comment") == ea_name]
        metrics = calculate_ea_metrics(ea_name, ea_trades, config)
        by_ea[ea_name] = metrics

        # Assign color
        if len(ea_names) <= len(EA_COLORS):
            ea_colors[ea_name] = EA_COLORS[i % len(EA_COLORS)]
        else:
            # Generate colors with HSL distributed uniformly
            hue = int((i / len(ea_names)) * 360)
            ea_colors[ea_name] = f"hsl({hue}, 70%, 60%)"

    # Portfolio uses only active EA trades
    active_trades = [t for t in closed_trades if t.get("comment") in set(ea_names)]
    portfolio = calculate_portfolio_metrics(active_trades, config)

    return {
        "portfolio": portfolio,
        "by_ea": by_ea,
        "ea_colors": ea_colors,
    }


def _empty_metrics(name, config):
    """Return empty MetricsResult for an EA with no trades."""
    mapping = config.get("mappings", {}).get(name, {})
    magic = mapping.get("magic")
    label = f"{magic} - {name}" if magic else name
    return {
        "ea_name": name,
        "magic": magic,
        "label": label,
        "instrument": mapping.get("instrument", "—"),
        "total_trades": 0,
        "winning_trades": 0,
        "losing_trades": 0,
        "long_trades": 0,
        "short_trades": 0,
        "long_wins": 0,
        "short_wins": 0,
        "net_profit": 0.0,
        "gross_profit": 0.0,
        "gross_loss": 0.0,
        "best_trade": 0.0,
        "worst_trade": 0.0,
        "avg_win": 0.0,
        "avg_loss": 0.0,
        "win_rate": 0.0,
        "profit_factor": 0.0,
        "payout_ratio": 0.0,
        "expectancy": 0.0,
        "max_dd_dollar": 0.0,
        "max_dd_pct": 0.0,
        "ret_dd": None,
        "recovery_factor": None,
        "sqn": None,
        "sqn_note": "",
        "sqn_label": "N/A",
        "sharpe_ratio": None,
        "weeks_operating": 0.0,
        "avg_duration_hours": 0.0,
        "stagnation_days": 0,
        "max_consec_wins": 0,
        "max_consec_losses": 0,
        "avg_consec_wins": 0.0,
        "avg_consec_losses": 0.0,
        "equity_curve": [],
        "drawdown_curve": [],
        "trades": [],
    }


def format_currency(val):
    """Format as $1,234.56 or -$1,234.56"""
    if val is None:
        return "N/A"
    if isinstance(val, str):
        return val
    sign = "-" if val < 0 else ""
    return f"{sign}${abs(val):,.2f}"


def format_pct(val, decimals=2):
    """Format as percentage string."""
    if val is None:
        return "N/A"
    if isinstance(val, str):
        return val
    return f"{val:.{decimals}f}%"


def format_metric(val, prefix="", suffix="", decimals=2):
    """Generic metric formatter."""
    if val is None:
        return "N/A"
    if isinstance(val, str):
        return val
    return f"{prefix}{val:.{decimals}f}{suffix}"
