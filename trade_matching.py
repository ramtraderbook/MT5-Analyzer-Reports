"""
trade_matching.py - Single source of truth for trade-to-EA identity resolution.

Both the web layer (ea_analyzer.py) and the decision layer (validator.py) need
to answer the same question: "does this trade belong to this EA?". Before this
module existed, each side answered it differently - the dashboard matched by
normalized comment, alias, or magic number, while the validator matched by
exact comment equality only. Same data, two different truths, and EAs whose
trade comment used spaces where the mapping key used underscores (or vice
versa) silently produced zero trades in the validator while rendering fine in
the dashboard.

This module is a stdlib-only leaf so both `validator.py` and `ea_analyzer.py`
can import it without creating an import cycle (validator.py cannot import
from ea_analyzer.py, since ea_analyzer.py already imports validator.py).
"""


def normalize_trade_key(value):
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def trade_matches_ea(trade, ea_name, config=None):
    comment = trade.get("comment", "")
    if normalize_trade_key(comment) == normalize_trade_key(ea_name):
        return True

    if config:
        mapping = config.get("mappings", {}).get(ea_name, {})
        alias = mapping.get("alias", "")
        magic = mapping.get("magic")
        if alias and normalize_trade_key(comment) == normalize_trade_key(alias):
            return True
        if magic is not None and normalize_trade_key(comment) == normalize_trade_key(magic):
            return True
    return False
