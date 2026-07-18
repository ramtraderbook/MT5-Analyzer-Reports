"""
test_weight_sensitivity.py - Tests for the 2E weight-sensitivity honesty flag
(validator.score_verdict_is_weight_sensitive / verdict_weight_sensitive).

The category weights (35/30/15/20) are an ungrounded spreadsheet port. The flag
reports when the score-driven verdict would flip under a +/-20% shift of any
weight. The core function is cross-checked against an INDEPENDENT brute-force
reweighting written here, so agreement is verified, not self-consistent.
"""

import itertools

import validator as v


# ── independent oracle: brute-force reweighting written from scratch ─────────

_W = (35, 30, 15, 20)   # w_riesgo, w_edge, w_caracter, w_desv
_TOTAL = sum(_W)


def _band(score):
    return "C" if score >= 70 else ("M" if score >= 45 else "E")


def _verdict_for(weights, subs):
    norm = _TOTAL / sum(weights)
    score = sum(w * norm * s for w, s in zip(weights, subs)) / 10.0
    return _band(round(score, 1))


def _brute_sensitive(subs, rel=0.2):
    base = _verdict_for(_W, subs)
    factors = (1 - rel, 1.0, 1 + rel)
    for combo in itertools.product(factors, repeat=4):
        if _verdict_for(tuple(w * f for w, f in zip(_W, combo)), subs) != base:
            return True
    return False


def test_agrees_with_independent_brute_force_over_a_grid():
    mismatches = []
    for sr in range(0, 11):
        for se in range(0, 11, 2):
            for sc in range(0, 11, 2):
                for sd in range(0, 11, 2):
                    subs = (sr, se, sc, sd)
                    got = v.score_verdict_is_weight_sensitive(*subs)
                    want = _brute_sensitive(subs)
                    if got != want:
                        mismatches.append((subs, got, want))
    assert not mismatches, f"{len(mismatches)} disagreements, e.g. {mismatches[:3]}"


# ── concrete cases ───────────────────────────────────────────────────────────


def test_flags_a_verdict_that_rests_on_the_exact_weights():
    # base score 62 (MONITOREAR) with a heavy spread: risk (weight 35) scores 0
    # while the rest score 9-10, so shifting the weighting tips it over a cut.
    assert v.score_verdict_is_weight_sensitive(0, 9, 10, 10) is True


def test_does_not_flag_a_robust_verdict():
    # base score 40 (ELIMINAR), far from the 45 cut and evenly spread
    assert v.score_verdict_is_weight_sensitive(1, 4, 7, 7) is False
    # all categories equal -> reweighting cannot move the score at all
    assert v.score_verdict_is_weight_sensitive(8, 8, 8, 8) is False


def test_none_subscore_is_not_sensitive():
    assert v.score_verdict_is_weight_sensitive(None, 9, 10, 10) is False


# ── the wrapper over a full analysis dict ────────────────────────────────────


def _analysis(veredicto, score, subs=(0, 9, 10, 10), sin_datos=False):
    return {
        "sin_datos": sin_datos,
        "score": score,
        "veredicto": veredicto,
        "s_riesgo": subs[0], "s_edge": subs[1], "s_caracter": subs[2], "s_desv": subs[3],
    }


def test_wrapper_flags_a_score_driven_sensitive_verdict():
    # score 62 -> band MONITOREAR; verdict matches -> score-driven -> assess
    assert v.verdict_weight_sensitive(_analysis("MONITOREAR", 62.0)) is True


def test_wrapper_ignores_override_forced_verdicts():
    # score 80 would band CONTINUAR, but the verdict is ELIMINAR (DD/PF override).
    # The kill does not depend on the weights, so it is never "weight sensitive".
    assert v.verdict_weight_sensitive(_analysis("ELIMINAR", 80.0)) is False


def test_wrapper_false_for_sin_datos_and_missing():
    assert v.verdict_weight_sensitive(_analysis("SIN DATOS", None, sin_datos=True)) is False
    assert v.verdict_weight_sensitive({"sin_datos": False, "score": None}) is False
    assert v.verdict_weight_sensitive(None) is False


def test_wrapper_false_for_robust_score_driven_verdict():
    assert v.verdict_weight_sensitive(_analysis("ELIMINAR", 40.0, subs=(1, 4, 7, 7))) is False
