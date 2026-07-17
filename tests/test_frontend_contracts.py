"""
test_frontend_contracts.py - Focused regression tests for the JD-6 frontend
audit findings across templates/validator_input.html, templates/validator.html,
templates/incubation_dashboard.html, templates/incubation_strategy.html,
templates/incubation_reference_edit.html and static/charts.js.

Templates are rendered through the real app.jinja_env (so `url_for`, filters
and autoescaping all behave exactly as in production) inside
app.test_request_context('/'). Structural HTML assertions use html5lib, which
implements the actual HTML5 tree-construction algorithm (nested <form> gets
foster-parented / dropped exactly like a real browser would), unlike Python's
stdlib html.parser which just reports tags as it sees them.

html5lib is not currently pinned in requirements.txt; it is already installed
in this environment. Per task instructions, since requirements.txt already
pins other test-only deps (pytest) directly (there is no separate
requirements-dev.txt), html5lib has been added there too.
"""

import re
from pathlib import Path

import html5lib
import pytest
from flask import url_for

from ea_analyzer import app

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _render(template_name, **context):
    context.setdefault("csrf_token", lambda: "tok")
    context.setdefault("sidebar_eas", [])
    with app.test_request_context("/"):
        return app.jinja_env.get_template(template_name).render(**context)


def _parse(html_text):
    """Parse with the real HTML5 tree-construction algorithm (no namespaces,
    so plain ElementTree tag names like 'form'/'input' can be used)."""
    return html5lib.parse(html_text, namespaceHTMLElements=False)


# ── Contract 1: a real 0 must survive the round-trip ────────────────────────


def test_zero_bt_values_render_as_zero_not_blank():
    """
    Regression: `entry.get('bt', {}).get('max_consec_losses', '') or ''` used
    to coerce a real backtest value of 0 (a perfectly valid "0 consecutive
    losses" / "0% win rate" / "0 days stagnation") to '' via Python's falsy-or.
    Re-saving that blank form field then stored None instead of 0, and the
    SIN DATOS completeness gate treated the checkpoint as missing data,
    flipping a real verdict to SIN DATOS. The template must render the
    literal "0", and a field that was genuinely never entered must still
    render blank so the two states stay distinguishable on the next save.
    """
    entry = {
        "bt": {"max_consec_losses": 0, "win_rate": 0, "stagnation_days": 0},
        "mc_retest": {},
        "mc_trades": {},
        "spp": {},
    }

    html = _render(
        "validator_input.html",
        magic="9001",
        ea_name="MyEA",
        ea_label="9001 - MyEA",
        entry=entry,
        live_preview=None,
        mappings={},
    )

    tree = _parse(html)
    inputs_by_name = {el.get("name"): el for el in tree.iter("input") if el.get("name")}

    for field in ("bt_max_consec_losses", "bt_win_rate", "bt_stagnation_days"):
        assert field in inputs_by_name, f"input {field} missing from rendered form"
        assert inputs_by_name[field].get("value") == "0", (
            f"{field} holds a real 0 and must render value=\"0\", got "
            f"{inputs_by_name[field].get('value')!r}"
        )

    # A key that was never entered (not present in entry["bt"] at all) must
    # stay blank -- otherwise 0 and "no data" become indistinguishable again.
    assert "bt_profit_factor" not in entry["bt"]
    assert inputs_by_name["bt_profit_factor"].get("value") == ""


# ── Contract 2: the delete form must not be nested inside the save form ─────


def test_delete_bt_form_is_separate_and_both_forms_carry_csrf():
    """
    Regression: the "Eliminar BT" delete <form> used to be physically nested
    inside the save <form>. The HTML5 tree-construction algorithm forbids
    nested <form> elements and silently drops the inner start tag (a real
    browser reparents its children into the outer form instead of creating a
    second form) -- so the page had exactly ONE form, "Eliminar BT" silently
    SAVED instead of deleting, and the confirm() dialog that lived on the
    dropped form's onsubmit never fired.
    """
    entry = {"bt": {}, "mc_retest": {}, "mc_trades": {}, "spp": {}}

    with app.test_request_context("/"):
        html = app.jinja_env.get_template("validator_input.html").render(
            csrf_token=lambda: "tok",
            sidebar_eas=[],
            magic="9001",
            ea_name="MyEA",
            ea_label="9001 - MyEA",
            entry=entry,
            live_preview=None,
            mappings={},
        )
        expected_edit_action = url_for("validator_edit", magic="9001")
        expected_delete_action = url_for("validator_delete", magic="9001")

    tree = _parse(html)
    forms = list(tree.iter("form"))

    assert len(forms) == 2, (
        f"expected exactly 2 forms (save + delete) once real HTML5 tree "
        f"construction runs, got {len(forms)} -- a nested <form> is silently "
        f"dropped by the parser"
    )

    actions = {f.get("action") for f in forms}
    assert actions == {expected_edit_action, expected_delete_action}

    for form in forms:
        csrf_inputs = [el for el in form.iter("input") if el.get("name") == "csrf_token"]
        assert csrf_inputs, f"form action={form.get('action')!r} is missing its csrf_token input"


# ── Contract 3: the CP2 dashboard legend must match the engine's thresholds ─


def test_cp2_dashboard_legend_matches_evaluate_cp2_thresholds():
    """
    Regression: templates/incubation_dashboard.html's CP2 explainer card used
    to read "2+ métricas -> OBSERVAR" / "4+ métricas -> ELIMINAR", which never
    matched evaluate_cp2()'s real failing_count thresholds
    (<=1 CONTINUAR, ==2 OBSERVAR, >=3 ELIMINAR) -- the UI was teaching users
    the wrong rule for their own verdicts. The thresholds are pulled straight
    out of incubation_validator.py's source so this test re-breaks the moment
    either side drifts again, instead of pinning two independent literals.
    """
    validator_src = (PROJECT_ROOT / "incubation_validator.py").read_text(encoding="utf-8")

    m_continue = re.search(
        r'if failing_count <= (\d+):\s*\n\s*verdict = "CONTINUAR"', validator_src
    )
    m_observe = re.search(
        r'elif failing_count == (\d+):\s*\n\s*verdict = "OBSERVAR"', validator_src
    )
    assert m_continue and m_observe, (
        "evaluate_cp2()'s verdict threshold shape changed -- update this "
        "test's parsing before trusting the comparison below"
    )

    continue_max = int(m_continue.group(1))
    observe_value = int(m_observe.group(1))
    eliminate_min = observe_value + 1

    dashboard_src = (PROJECT_ROOT / "templates" / "incubation_dashboard.html").read_text(
        encoding="utf-8"
    )
    card_match = re.search(
        r'>CP2</div>.*?(?=<div class="inc-explain-card">|\Z)', dashboard_src, re.DOTALL
    )
    assert card_match, "CP2 explainer card not found in incubation_dashboard.html"
    # Collapse whitespace so this stays robust to reflow/reindentation.
    card = re.sub(r"\s+", " ", card_match.group(0))

    continue_pattern = re.compile(rf"0-{continue_max}\s*métricas.*?CONTINUAR", re.DOTALL)
    observe_pattern = re.compile(rf"(?<!\d){observe_value}\s*métricas.*?OBSERVAR", re.DOTALL)
    # NOTE: use `.*?` (not `\D*?`) between the number and ELIMINAR -- the
    # intervening markup legitimately contains digits (e.g. the "#dc3545"
    # color hex code), which `\D` can never skip over.
    eliminate_pattern = re.compile(rf"(?<!\d){eliminate_min}(?!\d).*?ELIMINAR", re.DOTALL)

    assert continue_pattern.search(card), (
        f"expected '0-{continue_max} métricas ... CONTINUAR' in the CP2 card"
    )
    assert observe_pattern.search(card), (
        f"expected '{observe_value} métricas ... OBSERVAR' in the CP2 card"
    )
    assert eliminate_pattern.search(card), (
        f"expected '{eliminate_min} ... ELIMINAR' in the CP2 card"
    )

    # The stale pre-fix copy must be fully gone, not just supplemented.
    assert "2+" not in card
    assert "4+" not in card


# ── Contract 4: charts.js must escape EA names before innerHTML ────────────


def test_correlation_alert_chip_escapes_ea_names_amp_first():
    """
    Regression: EA names come straight from uploaded trade `comment` fields
    (attacker-controlled). renderCorrelationMatrix() built its alert-chip
    markup with `p.ea1` / `p.ea2` concatenated raw into a string later
    assigned via innerHTML -- an EA name like `<img src=x onerror=alert(1)>`
    would execute. Every occurrence of p.ea1/p.ea2 anywhere in the file must
    go through escapeHtml(), and escapeHtml() must replace '&' BEFORE '<' '>'
    '"' '\'' -- otherwise escaping '<' to '&lt;' first and then escaping '&'
    would double-escape it into '&amp;lt;'.
    """
    charts_src = (PROJECT_ROOT / "static" / "charts.js").read_text(encoding="utf-8")

    assert "escapeHtml(p.ea1)" in charts_src
    assert "escapeHtml(p.ea2)" in charts_src

    # No bare/unescaped occurrence anywhere in the file -- every p.ea1/p.ea2
    # token must be the argument of an escapeHtml() call.
    assert charts_src.count("p.ea1") == charts_src.count("escapeHtml(p.ea1)")
    assert charts_src.count("p.ea2") == charts_src.count("escapeHtml(p.ea2)")

    fn_match = re.search(r"function escapeHtml\(s\)\s*\{(.*?)\n\}", charts_src, re.DOTALL)
    assert fn_match, "escapeHtml() function not found in charts.js"
    body = fn_match.group(1)

    amp_pos = body.index("/&/")
    lt_pos = body.index("/</")
    gt_pos = body.index("/>/")
    quote_pos = body.index('/"/')
    apos_pos = body.index("/'/")

    assert amp_pos < lt_pos < gt_pos < quote_pos < apos_pos, (
        "escapeHtml must replace '&' before any entity it introduces "
        "(&lt; &gt; &quot; &#39;), or the '&' those entities contain gets "
        "escaped a second time"
    )


# ── Contract 5: no raw Jinja inside inline JS event-handler attributes ──────


def test_no_jinja_interpolation_inside_inline_event_handler_attributes():
    """
    Regression: a browser HTML-decodes an attribute VALUE before compiling
    the JS string it contains. Jinja's autoescaping turns an attacker
    apostrophe into `&#39;` inside onclick="...{{ ea_name }}...", but that
    decode step undoes the escaping before the JS parser ever sees it -- an
    EA/magic value containing a real `'` breaks out of the JS string literal
    and injects script. No onclick/onsubmit attribute in ANY template may
    contain a Jinja `{{ ... }}` expression; the value must be passed via a
    data-* attribute or a JS global instead (see validator.html's
    `.val-delete-bt-form` / incubation_reference_edit.html's `#ref-btn-delete`
    for the pattern this pins). Static confirm() strings with no `{{ }}` are
    fine and must still pass.
    """
    templates_dir = PROJECT_ROOT / "templates"
    handler_attr_re = re.compile(r'on(?:click|submit)="([^"]*)"')
    jinja_expr_re = re.compile(r"\{\{.*?\}\}")

    offenders = []
    for template_file in sorted(templates_dir.glob("*.html")):
        text = template_file.read_text(encoding="utf-8")
        for m in handler_attr_re.finditer(text):
            if jinja_expr_re.search(m.group(1)):
                offenders.append(f"{template_file.name}: {m.group(0)[:120]}")

    assert not offenders, (
        "Jinja expression(s) found inside an inline event-handler attribute: "
        + "; ".join(offenders)
    )


# ── Contract 6: an absent checkpoint score must not render as "None" ───────


def test_checkpoint_timeline_renders_dash_for_none_score():
    """
    Regression: incubation_strategy.html's checkpoint timeline printed
    `Score: {{ cp.score }}` directly. Checkpoints that haven't reached a
    scoring stage (PRE_CP1, or a CP1 hard-gate-only verdict) legitimately
    carry score=None, and Jinja renders None as the literal string "None" --
    users saw "Score: None" on the timeline. The real timeline fragment is
    extracted straight out of the template FILE (not hand-copied) and
    rendered through the actual Jinja engine, so this test tracks whatever
    that fragment actually contains rather than a frozen paraphrase of it.
    Rendering the full incubation_strategy.html page needs a large `m`
    metrics/comparison_rows/distribution fixture unrelated to this contract,
    so only the isolated `{% for cp in checkpoint_timeline %}` loop is
    rendered here.
    """
    strategy_src = (PROJECT_ROOT / "templates" / "incubation_strategy.html").read_text(
        encoding="utf-8"
    )
    fragment_match = re.search(
        r"\{% for cp in checkpoint_timeline %\}.*?\{% endfor %\}", strategy_src, re.DOTALL
    )
    assert fragment_match, "checkpoint_timeline loop not found in incubation_strategy.html"

    checkpoints = [
        {
            "label": "PRE_CP1",
            "verdict_class": "verdict-pending",
            "verdict": "Pending",
            "date": "01/01/2026",
            "trades": 3,
            "score": None,
            "details": "Not enough trades yet.",
        },
        {
            "label": "CP2",
            "verdict_class": "verdict-continue",
            "verdict": "CONTINUAR",
            "date": "02/01/2026",
            "trades": 25,
            "score": 72,
            "details": "All metrics within band.",
        },
    ]

    with app.test_request_context("/"):
        rendered = app.jinja_env.from_string(fragment_match.group(0)).render(
            checkpoint_timeline=checkpoints
        )

    assert "Score: 72" in rendered
    assert "Score: —" in rendered, "score=None must render the em-dash placeholder"
    assert "None" not in rendered


# ── Contract 7: an empty chart range must clear the stale empty-state msg ──


def test_every_chart_empty_state_path_clears_message_before_replot():
    """
    Regression: Plotly inserts its `.plot-container` as the div's first
    child and leaves foreign siblings alone, so a "Sin datos en este rango."
    `.chart-empty-msg` node injected on an earlier empty response stayed
    visible next to a chart that a later, non-empty response then plotted --
    every renderer with a chart-empty-msg branch must call
    `_clearChartEmptyMsg(...)` before its populated-path `Plotly.newPlot(...)`
    call. Kept as a source-level assertion: exercising the actual DOM/async
    fetch sequencing is out of reach without a real browser.
    """
    charts_src = (PROJECT_ROOT / "static" / "charts.js").read_text(encoding="utf-8")

    # Split into top-level function bodies so each renderer is checked in
    # isolation (a clear-call in one function must not "cover" another).
    starts = [m.start() for m in re.finditer(r"^(?:async )?function \w+\(", charts_src, re.MULTILINE)]
    starts.append(len(charts_src))
    blocks = [charts_src[starts[i] : starts[i + 1]] for i in range(len(starts) - 1)]

    checked_any = False
    for block in blocks:
        if "chart-empty-msg" not in block:
            continue
        checked_any = True
        fn_name = re.match(r"^(?:async )?function (\w+)\(", block).group(1)

        clear_positions = [m.start() for m in re.finditer(r"_clearChartEmptyMsg\(", block)]
        assert clear_positions, f"{fn_name}() has a chart-empty-msg path but never calls _clearChartEmptyMsg(...)"

        newplot_positions = [m.start() for m in re.finditer(r"Plotly\.newPlot\(", block)]
        for pos in newplot_positions:
            assert any(cp < pos for cp in clear_positions), (
                f"{fn_name}() calls Plotly.newPlot(...) without a preceding "
                f"_clearChartEmptyMsg(...) call -- a stale empty-state message "
                f"could survive next to freshly-plotted data"
            )

    assert checked_any, "no renderer with a chart-empty-msg path found -- fixture drifted from charts.js"


# ── Contract 7: the SQN "(orientativo)" hedge must render wherever the number
#    shows, and only when the sample is small (4C, honestidad de la interfaz) ──


def _sqn_fragment(path, pattern):
    src = (PROJECT_ROOT / path).read_text(encoding="utf-8")
    match = re.search(pattern, src, re.DOTALL)
    assert match, f"SQN fragment not found in {path}"
    return match.group(0)


# (template, regex to extract the SQN fragment, context builder taking sqn_note)
_SQN_SURFACES = [
    (
        "templates/strategy.html",
        r'\{% if m\.sqn is not none %\}\s*<div class="kpi-sub">.*?</div>\s*\{% endif %\}',
        lambda note: dict(m=dict(sqn=11.0, sqn_label="N/A", sqn_note=note)),
    ),
    (
        "templates/dashboard.html",
        r'<div\s+class="kpi-sub">\s*\{\{ portfolio\.sqn_label \}\}.*?</div>',
        lambda note: dict(portfolio=dict(sqn=11.0, sqn_label="N/A", sqn_note=note)),
    ),
    (
        "templates/dashboard.html",
        r'<td\s+class="mono th-center \{% if ea\.sqn.*?</td>',
        lambda note: dict(ea=dict(sqn=11.0, sqn_note=note)),
    ),
    (
        "templates/incubation_strategy.html",
        r'\{% if m\.sqn is not none and m\.sqn_note %\}.*?\{% endif %\}',
        lambda note: dict(m=dict(sqn=11.0, sqn_note=note)),
    ),
]


@pytest.mark.parametrize("path,pattern,ctx", _SQN_SURFACES)
def test_sqn_orientativo_hedge_shows_only_for_small_sample(path, pattern, ctx):
    """Regression (4C): with N < 20 the SQN is reported but its quality label is
    withheld and `sqn_note` carries "(orientativo)". Every surface that shows the
    SQN number must render that hedge with the distinct `.orientativo` class --
    it was dropped entirely on the per-EA table and the incubation strategy card,
    and styled identically to a solid subtitle elsewhere. With a large sample
    (`sqn_note` empty) the hedge must NOT appear.
    """
    fragment = _sqn_fragment(path, pattern)
    with app.test_request_context("/"):
        rendered_small = app.jinja_env.from_string(fragment).render(**ctx("(orientativo)"))
        rendered_large = app.jinja_env.from_string(fragment).render(**ctx(""))
    assert "orientativo" in rendered_small, (
        f"{path}: SQN hedge missing when sample is small (N<20)"
    )
    assert "orientativo" not in rendered_large, (
        f"{path}: SQN hedge shown when sample is large (must be clean)"
    )


# ── Contract 8: a thin-sample verdict must LOOK tentative -- the score ring and
#    the verdict badge desaturate and carry a "muestra chica" marker when
#    significance is Baja/Muy baja, so a KILL on 14 trades never renders as firm
#    as one on 500 (4B, honestidad de la interfaz) ────────────────────────────

_VALIDATOR_HTML = (PROJECT_ROOT / "templates" / "validator.html").read_text(encoding="utf-8")
# The row-level {% set signif_low %} lives outside these fragments; re-declare it
# so each extracted fragment renders with the same logic as production.
_SIGNIF_SET = "{% set signif_low = a.signif in ['Baja', 'Muy baja'] %}"
_RING_FRAG = _SIGNIF_SET + re.search(r'<div\s+class="val-score-ring.*?</div>', _VALIDATOR_HTML, re.DOTALL).group(0)
_BADGE_FRAG = _SIGNIF_SET + re.search(r'<span\s+class="val-badge val-\{\{ a\.veredicto.*?muestra chica</span>\s*\{% endif %\}', _VALIDATOR_HTML, re.DOTALL).group(0)


@pytest.mark.parametrize("signif,sin_datos,tentative", [
    ("Alta", False, False),
    ("Media", False, False),
    ("Baja", False, True),
    ("Muy baja", False, True),
    ("Muy baja", True, False),  # SIN DATOS already states the absence -- never double-mark
])
def test_thin_sample_verdict_looks_tentative(signif, sin_datos, tentative):
    a = dict(signif=signif, score=52.7, veredicto=("SIN DATOS" if sin_datos else "ELIMINAR"), sin_datos=sin_datos)
    with app.test_request_context("/"):
        badge = app.jinja_env.from_string(_BADGE_FRAG).render(a=a)
        # the ring is only reached when there IS a score (not sin_datos)
        ring = app.jinja_env.from_string(_RING_FRAG).render(a=a) if not sin_datos else ""
    assert ("is-tentative" in badge) is tentative, f"badge tentative mismatch for {signif}/{sin_datos}"
    assert ("muestra chica" in badge) is tentative, f"badge marker mismatch for {signif}/{sin_datos}"
    if not sin_datos:
        assert ("is-tentative" in ring) is tentative, f"ring tentative mismatch for {signif}"
