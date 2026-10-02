"""AP40 — static regression guard for the frontend's accessibility semantics.

This is NOT evidence that the app is accessible. It reads the JSX/CSS source and
fails if a fixed defect class comes back: an error surface without a live region,
a clickable <div>, an unlabelled control, a modal without its label, a missing
focus ring or reduced-motion guard. The evidence that the app actually works for
a keyboard and screen-reader user is the browser pass recorded in the AP40 plan
(keyboard-only walkthrough + axe-core report); axe and this file both cannot see
whether Escape really closes the dialog.

Each check also has a CONTROL: the scanner is run over a hand-written bad
snippet and must flag it, so a scanner that silently matches nothing cannot
pass the real files.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "frontend" / "src"
JSX_FILES = sorted(SRC.rglob("*.jsx"))
INDEX_CSS = SRC / "index.css"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _code(p: Path) -> str:
    """Source with comments blanked out (newlines kept, so line numbers hold):
    prose like "used to be a <div onClick>" must not count as markup."""
    src = _read(p)
    src = re.sub(r"/\*.*?\*/", lambda m: re.sub(r"[^\n]", " ", m.group(0)), src, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$", "", src)


# ── a tiny JSX opening-tag scanner ───────────────────────────────────────────
# A regex cannot find the end of `<div onClick={() => x > 1}>`, because the
# attribute values contain `>`. Walk the text, tracking {} depth and quotes.

def _opening_tags(src: str, names: tuple[str, ...]):
    """Yield (name, attrs_text, start_index) for each opening tag in `names`."""
    pat = re.compile(r"<(" + "|".join(names) + r")(?=[\s>/])")
    for m in pat.finditer(src):
        i, depth, quote = m.end(), 0, None
        while i < len(src):
            c = src[i]
            if quote:
                if c == quote:
                    quote = None
            elif c in "\"'`" and depth == 0:
                quote = c
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
            elif c == ">" and depth == 0:
                break
            i += 1
        yield m.group(1), src[m.end():i], m.start()


NON_INTERACTIVE = ("div", "span", "p", "li", "ul", "section", "article", "header",
                   "h1", "h2", "h3", "h4", "img", "label")


def _bare_clickables(src: str):
    """Non-interactive elements with onClick that lack the full button contract
    (role="button" + tabIndex + a key handler)."""
    bad = []
    for name, attrs, pos in _opening_tags(src, NON_INTERACTIVE):
        if "onClick" not in attrs:
            continue
        full_contract = ('role="button"' in attrs and "tabIndex" in attrs
                         and "onKeyDown" in attrs)
        if not full_contract:
            bad.append((name, src.count("\n", 0, pos) + 1))
    return bad


def _unlabelled_controls(src: str):
    bad = []
    for name, attrs, pos in _opening_tags(src, ("input", "select", "textarea")):
        if re.search(r'type="(hidden|submit|button)"', attrs):
            continue
        if "aria-label" in attrs or "aria-labelledby" in attrs:
            continue
        m = re.search(r'\bid=(?:"([^"]+)"|\{`([^`]+)`\})', attrs)
        if m:
            ident = m.group(1) or m.group(2)
            if f'htmlFor="{ident}"' in src or f"htmlFor={{`{ident}`}}" in src:
                continue
        # Wrapped in a <label>…</label> (implicit label).
        before = src[:pos]
        if before.rfind("<label") > before.rfind("</label>"):
            continue
        bad.append((name, src.count("\n", 0, pos) + 1))
    return bad


# ── controls: the scanners can fail ──────────────────────────────────────────

def test_control_scanner_flags_a_bare_clickable_div():
    snippet = '<div className="x" onClick={() => go(a > b)}>hi</div>'
    assert _bare_clickables(snippet) == [("div", 1)]


def test_control_scanner_accepts_the_full_button_contract():
    snippet = '<article role="button" tabIndex={0} onKeyDown={k} onClick={go}>x</article>'
    assert _bare_clickables(snippet) == []


def test_control_scanner_flags_an_unlabelled_input():
    assert _unlabelled_controls('<input type="text" placeholder="Name" />') == [("input", 1)]
    assert _unlabelled_controls('<label>Name <input type="text" /></label>') == []
    assert _unlabelled_controls('<label htmlFor="n">N</label><input id="n" />') == []


# ── the real source ─────────────────────────────────────────────────────────

def test_the_scanner_actually_sees_the_source():
    """A glob that matched nothing would make every check below vacuous."""
    assert len(JSX_FILES) >= 15, JSX_FILES
    total_controls = sum(len(list(_opening_tags(_code(p), ("input", "select", "textarea"))))
                         for p in JSX_FILES)
    assert total_controls >= 11, total_controls


@pytest.mark.parametrize("path", JSX_FILES, ids=lambda p: p.name)
def test_no_clickable_non_interactive_elements(path):
    assert _bare_clickables(_code(path)) == []


@pytest.mark.parametrize("path", JSX_FILES, ids=lambda p: p.name)
def test_every_form_control_has_a_programmatic_label(path):
    assert _unlabelled_controls(_code(path)) == []


def test_error_and_status_surfaces_are_live_regions():
    alerts = sum(len(re.findall(r'role=(?:"alert"|\{[^}]*\'alert\')', _read(p))) for p in JSX_FILES)
    live_alert_uses = sum(_read(p).count("<LiveAlert") for p in JSX_FILES)
    statuses = sum(len(re.findall(r'role=(?:"status"|\{[^}]*\'status\')', _read(p)))
                   for p in JSX_FILES)
    assert alerts + live_alert_uses >= 10, (alerts, live_alert_uses)
    assert statuses >= 8, statuses


def test_error_message_class_only_renders_inside_an_alert():
    """`.error-message` is the app's error styling; it may only be applied by
    LiveAlert, whose container is role="alert"."""
    offenders = [p.name for p in JSX_FILES
                 if 'className="error-message"' in _read(p) and p.name != "LiveAlert.jsx"]
    assert offenders == []
    live_alert = _read(SRC / "components" / "LiveAlert.jsx")
    assert 'role="alert"' in live_alert


def test_quiz_modal_is_a_labelled_trapped_dialog():
    quiz = _read(SRC / "components" / "QuizModal.jsx")
    # The third argument is the focus fallback for when the trigger is gone
    # (passing the quiz completes the milestone and removes its button).
    assert "useFocusTrap(dialogRef, onClose, returnFocusId)" in quiz
    assert 'role="dialog"' in quiz and 'aria-modal="true"' in quiz
    assert 'aria-labelledby="quiz-modal-title"' in quiz
    # One heading carries the id in EACH phase (loading, error, taking, result),
    # so the label always points at a real element.
    assert quiz.count('id="quiz-modal-title"') == 4


def test_focus_trap_handles_escape_tab_and_restores_focus():
    hook = _read(SRC / "hooks" / "useFocusTrap.js")
    assert "'Escape'" in hook
    assert "'Tab'" in hook
    assert "previouslyFocused.focus()" in hook
    assert "container.focus()" in hook


def test_reduced_motion_block_covers_every_animation():
    css = _read(INDEX_CSS)
    m = re.search(r"@media\s*\(prefers-reduced-motion:\s*reduce\)\s*\{(.*?)\n\}", css, re.S)
    assert m, "no prefers-reduced-motion block in index.css"
    block = m.group(1)
    assert re.search(r"^\s*\*,", block, re.M), "the guard must be universal (*)"
    assert "animation-duration" in block and "transition-duration" in block
    # Count what it has to cover, so a new animation is noticed (AP40 found 8).
    css_files = [INDEX_CSS] + sorted((SRC / "components").glob("*.css"))
    animations = sum(len(re.findall(r"^\s*animation\s*:", _read(p), re.M)) for p in css_files)
    assert animations >= 8


def _luminance(hex_colour: str) -> float:
    h = hex_colour.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))

    def lin(c):
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def _contrast(a: str, b: str) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def test_contrast_helper_control():
    assert round(_contrast("#ffffff", "#000000"), 1) == 21.0
    assert _contrast("#777777", "#888888") < 3


def test_focus_ring_exists_and_contrasts_with_every_surface_it_sits_on():
    css = _read(INDEX_CSS)
    assert re.search(r"^:focus-visible\s*\{", css, re.M), "no app-wide :focus-visible rule"
    ring = re.search(r"--focus-ring:\s*(#[0-9a-fA-F]{6})", css).group(1)
    # The dark surfaces a focused control sits on (outline-offset leaves a gap of
    # the surface colour, so these are the ring's adjacent colours).
    surfaces = ["#0a0a0f", "#13131a", "#1a1a24", "#1e293b"]
    for s in surfaces:
        assert _contrast(ring, s) >= 3.0, (ring, s, _contrast(ring, s))


# AP48 — text colours that measured below AA 4.5:1 on this app's dark surfaces
# (#64748b is 3.07-4.15:1, #6b7280 3.72:1, #6b6b7b 2.8-3.78:1). Borders may use
# them; TEXT may not. This catches `color: '#64748b'` (JSX) and `color: #64748b`
# (CSS); a `background`/`border` with the same hex is fine.
_LOW_CONTRAST = ("#64748b", "#6b7280", "#6b6b7b")


def _low_contrast_text(src: str):
    pat = re.compile(r"(?<![-\w])color\s*:\s*['\"]?(" + "|".join(_LOW_CONTRAST) + r")", re.I)
    return [src.count("\n", 0, m.start()) + 1 for m in pat.finditer(src)]


def test_low_contrast_scanner_control():
    assert _low_contrast_text("a { color: #64748b; }") == [1]
    assert _low_contrast_text("style={{ color: '#6b7280' }}") == [1]
    assert _low_contrast_text("a { border-color: #64748b; background: #6b7280; }") == []


@pytest.mark.parametrize("path", JSX_FILES + sorted(SRC.rglob("*.css")), ids=lambda p: p.name)
def test_no_low_contrast_text_colours(path):
    assert _low_contrast_text(_code(path)) == []
