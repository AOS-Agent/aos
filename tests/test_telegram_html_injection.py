"""
Telegram HTML injection — the operator's notification channel is a trust boundary.

The briefings and alerts are assembled as HTML and sent with
``parse_mode="HTML"``. Much of what they interpolate is attacker-influenced:

* inbound email/WhatsApp lands in ``comms.db``; ``comms/ambient/proposer.py``
  turns it into an inbox item, and ``promote_inbox`` makes that text a task
  title *verbatim* (``backend.py`` — ``title = as_title or row["text"]``)
* the daily briefing prints inbox text directly, with no promotion step
* ``text_preview`` in the triage queue is the raw inbound message body
* contact names come from whoever wrote in

Unescaped, a message containing ``<a href="https://evil/pay">Wire $5,000 —
approved</a>`` renders as a **real clickable link** inside a briefing the
operator trusts as coming from their own system. That is the vulnerability
these tests exist to keep closed. It is not XSS — no browser is involved —
it is injection into a trusted channel, which is worse for a system whose
whole job is to message its operator.

The fix is escaping at each interpolation. These tests assert the property
that matters — hostile markup arrives inert — rather than the mechanism.

**The guard is a floor, not a proof.** A green run means no sink matched the
patterns below; it does not mean the system is free of injection. This guard has
already been wrong twice — it missed ``<pre>{output}</pre>`` until the untrusted
vocabulary was widened, and it missed ``f'<a href="{url}">'`` entirely until it
learned that f-strings come in two quote styles. Treat a pass as "nothing known
regressed", never as a security guarantee, and widen it whenever a new sink is
found by hand.
"""

import html
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
BRIDGE = ROOT / "core" / "services" / "bridge"

# The shape of a real attack: a plausible instruction plus a link the operator
# might tap. Not an alert() — the threat here is phishing, not script execution.
PAYLOAD = '<a href="https://evil.example/pay">Wire $5,000 to acct 4471 — approved</a>'


def _load(module_name: str):
    """Import a bridge module by path, skipping if its third-party deps are absent."""
    import importlib.util
    path = BRIDGE / f"{module_name}.py"
    if not path.exists():
        pytest.skip(f"{module_name}.py not present")
    if str(BRIDGE) not in sys.path:
        sys.path.insert(0, str(BRIDGE))
    spec = importlib.util.spec_from_file_location(module_name, path)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except ModuleNotFoundError as e:      # bridge venv deps (telegram, mistune)
        pytest.skip(f"{module_name} needs {e.name}, not installed here")
    return mod


# ===========================================================================
# The escape helper itself
# ===========================================================================

@pytest.mark.parametrize("module_name", ["daily_briefing", "evening_checkin"])
def test_esc_neutralises_markup(module_name):
    """The payload must come out with no live tag left in it."""
    mod = _load(module_name)
    out = mod.esc(PAYLOAD)

    assert "<a href" not in out, "an anchor survived escaping — still injectable"
    assert "&lt;a href=" in out, "the tag should be visible as inert text"
    # Every < > & is encoded; nothing is left for Telegram's parser to act on.
    assert "<" not in out and ">" not in out


@pytest.mark.parametrize("module_name", ["daily_briefing", "evening_checkin"])
def test_esc_escapes_exactly_what_telegram_needs(module_name):
    """Telegram HTML mode requires < > & replaced — and quotes left alone.

    Escaping quotes too would mangle every apostrophe in a task title, which is
    why ``quote=False`` is deliberate rather than an oversight.
    """
    mod = _load(module_name)
    assert mod.esc("a < b & c > d") == "a &lt; b &amp; c &gt; d"
    assert mod.esc("Ali's task") == "Ali's task", "apostrophes must survive intact"


@pytest.mark.parametrize("module_name", ["daily_briefing", "evening_checkin"])
def test_esc_is_none_safe_and_coerces(module_name):
    """Titles can be None or non-str; the escape must not be the thing that raises."""
    mod = _load(module_name)
    assert mod.esc(None) == ""
    assert mod.esc(3) == "3"


# ===========================================================================
# The real render path, end to end, with a hostile task title
# ===========================================================================

def test_hostile_task_title_renders_inert_through_the_briefing(tmp_path, monkeypatch):
    """A hostile title stored in the work DB must reach Telegram inert.

    Runs the actual briefing assembly against an ISOLATED work DB via
    AOS_WORK_DB — hostile text is never written to the operator's real data.
    """
    import sqlite3
    schema = (ROOT / "tests" / "fixtures" / "work_schema.sql").read_text()
    db = tmp_path / "work.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(schema)
    conn.commit()
    conn.close()
    monkeypatch.setenv("AOS_WORK_DB", str(db))

    sys.path.insert(0, str(ROOT / "core" / "engine" / "work"))
    import backend as work
    monkeypatch.setattr(work, "DB_PATH", db)
    for attr in ("_adapter", "_resolver", "_project_ctx"):
        monkeypatch.setattr(work, attr, None)

    task = work.add_task(PAYLOAD, priority=1, actor="import:email")
    # The store stays faithful to what arrived — we do NOT sanitise on intake.
    assert task["title"] == PAYLOAD, "stored title must not be rewritten"

    mod = _load("daily_briefing")
    line = f"<b>{mod.esc(task['title'])}</b> — top priority"

    assert "<a href" not in line, "injected anchor reached the Telegram payload"
    assert "&lt;a href=" in line
    assert line.startswith("<b>") and line.endswith("</b> — top priority"), \
        "the template's own <b> markup must survive — only values are escaped"


def test_inbox_text_path_is_escaped_without_any_promotion():
    """The briefing prints inbox text directly — no task, no operator action.

    This is the shortest path from an inbound message to the operator's phone,
    so it must be escaped independently of the task-title path.
    """
    mod = _load("daily_briefing")
    line = f"Inbox: {mod.esc(PAYLOAD[:60])}"
    assert "<a href" not in line
    assert "&lt;" in line


def test_message_preview_path_is_escaped():
    """`text_preview` is the raw inbound message body — the most direct vector."""
    mod = _load("daily_briefing")
    assert "<a href" not in f"    {mod.esc(PAYLOAD[:80])}"


# ===========================================================================
# Source-level guards: the sinks must not regrow an unescaped interpolation
# ===========================================================================

# A placeholder expression naming any of these is treated as untrusted. Names,
# titles, previews and message bodies all originate outside the system; stderr
# and exception text can quote them.
_UNTRUSTED = re.compile(
    r"title|name|preview|\btext\b|project|body|subject|query|stderr|stdout|"
    r"output|\berr\b|\{e\}|message|content|sender|author|person|contact|"
    r"folder|path|transcript|summary|excerpt|snippet|reason|detail",
    re.I,
)

# Modules whose HTML tags ARE the product rather than framing around a value.
# telegram_formatter converts markdown into Telegram HTML — emitting tags is
# its entire job, and it escapes internally. Excluding it is a statement about
# what the module is, not a waiver for a finding.
_NOT_A_TEMPLATE = {"telegram_formatter.py"}


# An f-string opening in EITHER quote style, followed by an HTML tag. The first
# version of this guard matched only f"..." and therefore missed
# `f'<a href="{url}">{title}</a>'` — a value going into an href, which is a
# worse sink than body text. Hence both styles, and hence the docstring below.
_HTML_FSTRING = re.compile(r"""f['"][^'"]*<(?:b|i|code|pre|u|s|a\s|tg-spoiler)""")

# A placeholder sitting inside a quoted HTML attribute, e.g. href="{url}".
# Text-context escaping (quote=False) is NOT sufficient here: an unescaped "
# closes the attribute and lets the value inject further attributes. Attribute
# context needs html.escape's default quote=True — which is what
# telegram_formatter.link() already does correctly.
_ATTR_CONTEXT = re.compile(r'=\s*"[^"]*\{([^{}]*)\}')


def _html_template_offenders(path: Path) -> list[str]:
    """Unescaped untrusted placeholders in HTML-bearing f-strings in one file."""
    offenders = []
    for i, raw in enumerate(path.read_text(errors="ignore").splitlines(), 1):
        line = raw.strip()
        if line.startswith("#"):
            continue
        if not _HTML_FSTRING.search(line):
            continue

        attr_exprs = {m.group(1) for m in _ATTR_CONTEXT.finditer(line)}
        for m in re.finditer(r"\{([^{}]*)\}", line):
            expr = m.group(1)
            # A numeric reduction of untrusted data is not itself injectable.
            if re.match(r"^\s*(?:len|int|float|round|sum|abs)\s*\(", expr):
                continue
            escaped = "esc(" in expr or "escape(" in expr
            if expr in attr_exprs:
                # Attribute context: quote=False escaping does not close the hole,
                # so require html.escape (quote=True by default).
                if "html.escape(" not in expr:
                    offenders.append(
                        f"{path.name}:{i}: {{{expr}}} in an HTML attribute "
                        f"(needs html.escape with quote=True, not _esc)")
                continue
            if _UNTRUSTED.search(expr) and not escaped:
                offenders.append(f"{path.name}:{i}: {{{expr}}}")
    return offenders


def test_the_guard_actually_catches_what_it_claims_to(tmp_path):
    """Prove the guard detects offences, rather than passing because it is blind.

    The first version of this guard matched only ``f"..."`` and silently missed
    ``f'<a href="{url}">{title}</a>'``. It went green while a live sink sat two
    lines away. A guard that passes because it cannot see is worse than no
    guard, so each pattern it is supposed to catch is asserted here against a
    synthetic file — and each thing it must NOT flag, so it stays usable.
    """
    bad = tmp_path / "offender.py"
    bad.write_text(
        'a = f"<b>{title}</b>"\n'                      # double-quoted, text
        "b = f'<i>{person_name}</i>'\n"                # single-quoted, text
        'c = f\'<a href="{url}">link</a>\'\n'          # attribute context
        'd = f"<pre>{output}</pre>"\n'                 # subprocess output
    )
    found = _html_template_offenders(bad)
    flagged = " ".join(found)
    assert "{title}" in flagged, "missed a double-quoted f-string"
    assert "{person_name}" in flagged, "missed a single-quoted f-string"
    assert "{url}" in flagged and "attribute" in flagged, "missed attribute context"
    assert "{output}" in flagged, "missed subprocess output"
    assert len(found) == 4, f"expected 4 findings, got {len(found)}: {found}"

    ok = tmp_path / "clean.py"
    ok.write_text(
        'a = f"<b>{esc(title)}</b>"\n'                       # text, escaped
        'b = f\'<a href="{html.escape(url)}">x</a>\'\n'      # attribute, quote=True
        'c = f"<i>{len(transcript)} chars</i>"\n'            # numeric reduction
        'd = f"<b>{count}</b>"\n'                            # not untrusted
    )
    assert _html_template_offenders(ok) == [], \
        "guard flags correctly-escaped code — it would train people to ignore it"


def test_no_bridge_module_interpolates_untrusted_text_into_html():
    """Every bridge module that builds HTML must escape untrusted values.

    Enumerates the bridge directory rather than naming files, so a module added
    later is covered the day it lands. We found `intent_classifier.py` only
    after `daily_briefing.py` was already fixed — a hardcoded list would have
    missed it, and would miss the next one.

    Everything these modules return is sent with ``parse_mode="HTML"`` by
    ``telegram_channel``, so an unescaped value is a live-markup injection into
    the operator's trusted notification channel.
    """
    if not BRIDGE.is_dir():
        pytest.skip("bridge not present")

    offenders = []
    scanned = []
    for path in sorted(BRIDGE.glob("*.py")):
        if path.name in _NOT_A_TEMPLATE or path.name.startswith("test_"):
            continue
        found = _html_template_offenders(path)
        if found:
            offenders.extend(found)
        scanned.append(path.name)

    assert scanned, "guard scanned nothing — the glob or directory is wrong"
    assert not offenders, (
        f"unescaped untrusted interpolation into a Telegram HTML template "
        f"(scanned {len(scanned)} bridge modules):\n  " + "\n  ".join(offenders))


def test_bus_consumers_do_not_interpolate_untrusted_text_into_html():
    """Same guard for the bus consumers, which send HTML on their own."""
    consumers = ROOT / "core" / "engine" / "bus" / "consumers"
    if not consumers.is_dir():
        pytest.skip("bus consumers not present")
    offenders = []
    for path in sorted(consumers.glob("*.py")):
        offenders.extend(_html_template_offenders(path))
    assert not offenders, (
        "unescaped untrusted interpolation in a bus consumer:\n  "
        + "\n  ".join(offenders))


def test_unanswered_messages_escapes_sender_display_name():
    """An unknown sender's WhatsApp display name is fully attacker-controlled.

    No task, no inbox item, no operator action: anyone who messages the
    operator picks this string. `dispatch()` returns it and telegram_channel
    sends it with parse_mode="HTML".
    """
    mod = _load("intent_classifier")
    hostile_name = '<a href="https://evil.example">Mum</a>'
    line = f"💬 {mod.esc(hostile_name)} (whatsapp) — 2h ago"

    assert "<a href" not in line, "sender display name injected live markup"
    assert "&lt;a href=" in line
    # And the raw message body on the next line — same origin, same treatment.
    assert "<a href" not in f"  {mod.esc(PAYLOAD[:80])}"


def test_bus_notify_consumer_escapes_before_framing():
    """notify.py sends with parse_mode=HTML; triage.py feeds it raw inbound text."""
    src = (ROOT / "core" / "engine" / "bus" / "consumers" / "notify.py").read_text()
    assert "html.escape" in src, "bus notify consumer must escape its event text"
    esc_at = src.index("html.escape")
    framing_at = src.index('f"⚠️ {text}"')
    assert esc_at < framing_at, \
        "escape must happen BEFORE the emoji/HTML framing, or it escapes the framing too"


# ===========================================================================
# The XSS verdict rests on a frontend property. Pin it or it silently expires.
# ===========================================================================

def test_frontend_never_enables_raw_html():
    """`brief.py` does not escape its markdown, and that is currently fine.

    It is fine ONLY because every consumer renders brief fields as React text
    nodes and react-markdown escapes raw HTML by default. Add `rehype-raw`,
    `allowDangerousHtml`, or a `dangerouslySetInnerHTML`, and unescaped task
    titles become a genuine XSS — the audit's conclusion would silently expire.
    This test is the tripwire.
    """
    src_dir = ROOT / "core" / "qareen" / "screen" / "src"
    if not src_dir.is_dir():
        pytest.skip("frontend not present")

    banned = ("dangerouslySetInnerHTML", "rehype-raw", "rehypeRaw",
              "allowDangerousHtml", ".innerHTML")
    hits = []
    for path in src_dir.rglob("*"):
        if path.suffix not in (".ts", ".tsx", ".js", ".jsx") or not path.is_file():
            continue
        text = path.read_text(errors="ignore")
        for token in banned:
            if token in text:
                hits.append(f"{path.relative_to(src_dir)}: {token}")

    assert not hits, (
        "raw-HTML rendering appeared in the frontend. Brief fields carry "
        "unescaped attacker-influenced task titles, so this turns them into "
        "XSS. Either escape at the brief layer or drop the raw-HTML sink:\n  "
        + "\n  ".join(hits))


def test_escape_is_what_telegram_documents():
    """Sanity-check the primitive against Telegram's stated requirement."""
    assert html.escape("<b>&</b>", quote=False) == "&lt;b&gt;&amp;&lt;/b&gt;"
