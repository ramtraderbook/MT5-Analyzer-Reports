"""Pins for parse_reference_form's all-or-nothing required-section validation.

This is the root cause of the audit's most reachable CRITICAL: read_field used
to enforce required fields with `if required and section_key == "backtest"`, so
the `mc_manipulation_95` / `mc_retest_95` sections were declared required: True
and never actually validated. A blank MC95 field was silently dropped, passed
reference_ready (which only checks for a non-empty dict), and reached
_hard_gates, where the missing key defaulted to 0 and made the gate
unsatisfiable -- eliminating a healthy EA.

The engines now return SIN DATOS instead, but that is the safety net. These
tests pin the form-layer fix so a partially filled required section cannot be
saved in the first place.
"""

from incubation_domain import INCUBATION_REFERENCE_SECTIONS, parse_reference_form


def _fields_for(section_key):
    for section in INCUBATION_REFERENCE_SECTIONS:
        if section["key"] == section_key:
            return [field["key"] for field in section["fields"]]
    raise AssertionError(f"unknown section: {section_key}")


def _filled(section_key, value="1"):
    """Every field of a section filled with a parseable value."""
    return {
        f"{section_key}_{field}": ("2020.01.01 - 2024.01.01" if field == "bt_period" else value)
        for field in _fields_for(section_key)
    }


def _complete_form():
    form = {}
    form.update(_filled("backtest"))
    form.update(_filled("mc_manipulation_95"))
    form.update(_filled("mc_retest_95"))
    return form


def test_complete_form_has_no_errors():
    _, errors, _ = parse_reference_form(_complete_form())
    assert errors == {}


def test_partially_filled_mc95_section_is_rejected():
    """The exact shape that used to save silently and then eliminate a healthy EA."""
    form = _complete_form()
    blanked = "mc_manipulation_95_max_consec_losses"
    assert blanked in form, "fixture drifted from the section schema"
    form[blanked] = ""

    _, errors, _ = parse_reference_form(form)

    assert "mc_manipulation_95.max_consec_losses" in errors
    assert "obligatorio" in errors["mc_manipulation_95.max_consec_losses"]


def test_every_blank_field_in_a_touched_required_section_is_reported():
    """All-or-nothing: one entered value makes the whole section mandatory."""
    form = _complete_form()
    fields = _fields_for("mc_retest_95")
    for field in fields[1:]:
        form[f"mc_retest_95_{field}"] = ""

    _, errors, _ = parse_reference_form(form)

    for field in fields[1:]:
        assert f"mc_retest_95.{field}" in errors
    assert f"mc_retest_95.{fields[0]}" not in errors


def test_fully_empty_mc_section_is_allowed_because_mc95_sections_are_either_or():
    """A section nobody touched stays optional; only the 'at least one MC95' rule applies."""
    form = {}
    form.update(_filled("backtest"))
    form.update(_filled("mc_manipulation_95"))
    # mc_retest_95 omitted entirely.

    _, errors, warnings = parse_reference_form(form)

    assert errors == {}
    assert any("Retest Methods" in w for w in warnings)


def test_both_mc95_sections_empty_is_rejected():
    _, errors, _ = parse_reference_form(_filled("backtest"))
    assert "monte_carlo" in errors


def test_backtest_section_stays_enforced():
    """Regression: the one section that was always validated must remain so."""
    form = _complete_form()
    form["backtest_win_rate"] = ""

    _, errors, _ = parse_reference_form(form)

    assert "backtest.win_rate" in errors


def test_optional_sections_accept_partial_entry():
    """required: False sections (MC50, SPP) must not trigger all-or-nothing."""
    form = _complete_form()
    mc50_fields = _fields_for("mc_manipulation_50")
    form[f"mc_manipulation_50_{mc50_fields[0]}"] = "5"

    _, errors, _ = parse_reference_form(form)

    assert errors == {}


def test_non_numeric_value_is_reported_separately_from_blankness():
    form = _complete_form()
    form["mc_manipulation_95_max_dd_pct"] = "abc"

    _, errors, _ = parse_reference_form(form)

    assert "número válido" in errors["mc_manipulation_95.max_dd_pct"]


def test_rejected_form_does_not_yield_a_partial_mc95_payload():
    """The payload must not carry the half-filled section that caused the CRITICAL."""
    form = _complete_form()
    form["mc_manipulation_95_max_consec_losses"] = ""

    data, errors, _ = parse_reference_form(form)

    assert errors, "a partially filled required section must not validate"
    assert "max_consec_losses" not in data["mc_manipulation"]["confidence_95"]
