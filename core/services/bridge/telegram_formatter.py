"""Convert Claude's Markdown output to Telegram-compatible HTML.

Telegram supports a limited HTML subset: <b>, <i>, <u>, <s>, <code>, <pre>,
<a>, <blockquote>, <tg-spoiler>. This module uses mistune 3 to parse
GitHub-flavored Markdown and render it into that subset.

This is Telegram-specific — Slack and other channels have their own formatting.
"""

import html
import re

import mistune
from mistune.plugins.formatting import strikethrough
from mistune.plugins.table import table


class TelegramHTMLRenderer(mistune.HTMLRenderer):
    """Renders Markdown AST to Telegram-safe HTML."""

    def __init__(self):
        super().__init__()
        self._list_is_ordered = False
        self._list_item_index = 0
        self._table_rows: list[list[str]] = []
        self._table_current_row: list[str] = []
        self._table_in_head = False

    # ── Inline ──────────────────────────────────────────────

    def text(self, text: str) -> str:
        return html.escape(text)

    def strong(self, text: str) -> str:
        return f"<b>{text}</b>"

    def emphasis(self, text: str) -> str:
        return f"<i>{text}</i>"

    def codespan(self, text: str) -> str:
        return f"<code>{html.escape(text)}</code>"

    def linebreak(self) -> str:
        return "\n"

    def softbreak(self) -> str:
        # Soft break = space (standard inline rendering). Hard breaks use linebreak().
        return " "

    def link(self, text: str, url: str, title: str | None = None) -> str:
        safe = html.escape(url)
        return f'<a href="{safe}">{text}</a>'

    def image(self, text: str, url: str, title: str | None = None) -> str:
        # Telegram can't render inline images — show as link
        safe = html.escape(url)
        alt = text or "image"
        return f'<a href="{safe}">[{alt}]</a>'

    def inline_html(self, raw: str) -> str:
        return html.escape(raw)

    # ── Block ───────────────────────────────────────────────

    def paragraph(self, text: str) -> str:
        return f"{text}\n\n"

    def heading(self, text: str, level: int, **attrs) -> str:
        # Telegram has no heading tags — render as bold
        return f"<b>{text}</b>\n\n"

    def blank_line(self) -> str:
        return "\n"

    def thematic_break(self) -> str:
        return "———\n\n"

    def block_code(self, code: str, info: str | None = None) -> str:
        escaped = html.escape(code)
        if info:
            lang = html.escape(info.split()[0])
            return f'<pre><code class="language-{lang}">{escaped}</code></pre>\n\n'
        return f"<pre>{escaped}</pre>\n\n"

    def block_quote(self, text: str) -> str:
        return f"<blockquote>{text.strip()}</blockquote>\n\n"

    def block_html(self, raw: str) -> str:
        return html.escape(raw)

    def block_text(self, text: str) -> str:
        return text

    def block_error(self, text: str) -> str:
        return html.escape(text)

    def render_token(self, token, state):
        # Set ordered flag before list items are rendered
        if token["type"] == "list":
            self._list_is_ordered = token.get("attrs", {}).get("ordered", False)
            self._list_item_index = 0
        return super().render_token(token, state)

    def list(self, text: str, ordered: bool, **attrs) -> str:
        self._list_is_ordered = False
        self._list_item_index = 0
        return f"{text}\n"

    def list_item(self, text: str) -> str:
        clean = text.strip()
        self._list_item_index += 1
        if self._list_is_ordered:
            return f"  {self._list_item_index}. {clean}\n"
        return f"  • {clean}\n"

    # ── Tables ────────────────────────────────────────────────
    # Telegram has no <table> support — render as monospaced pre block

    def table(self, text: str) -> str:
        rows = self._table_rows
        self._table_rows = []
        self._table_current_row = []
        if not rows or len(rows) < 2:
            return text

        # Render as simple list — tables don't work well on phone screens.
        # First row = headers, rest = data rows.
        headers = rows[0]
        lines = []
        for row in rows[1:]:
            parts = []
            for i, cell in enumerate(row):
                if not cell.strip():
                    continue
                if i < len(headers) and headers[i].strip():
                    parts.append(f"<b>{headers[i]}</b>: {cell}")
                else:
                    parts.append(cell)
            lines.append(" · ".join(parts))

        return "\n".join(lines) + "\n\n"

    def table_head(self, text: str) -> str:
        if self._table_current_row:
            self._table_rows.append(self._table_current_row)
            self._table_current_row = []
        return text

    def table_body(self, text: str) -> str:
        return text

    def table_row(self, text: str) -> str:
        self._table_rows.append(self._table_current_row)
        self._table_current_row = []
        return text

    def table_cell(self, text: str, align=None, head=False) -> str:
        # Unescape HTML entities so we store plain text (avoid double-escaping)
        clean = re.sub(r"<[^>]+>", "", text)
        clean = html.unescape(clean)
        self._table_current_row.append(clean)
        return text

    # ── Strikethrough (plugin) ──────────────────────────────

    def strikethrough(self, text: str) -> str:
        return f"<s>{text}</s>"


# Collapse 3+ consecutive newlines into 2
_EXCESS_NEWLINES = re.compile(r"\n{3,}")


def md_to_telegram_html(text: str) -> str:
    """Convert a Markdown string to Telegram-compatible HTML.

    Returns cleaned HTML suitable for parse_mode="HTML".
    Handles partial/streaming markdown gracefully.
    """
    if not text:
        return text

    # Close unclosed code fences before parsing (common during streaming)
    fence_count = text.count("```")
    if fence_count % 2 == 1:
        text = text + "\n```"

    try:
        # Fresh renderer per call — avoids stale table state between invocations
        renderer = TelegramHTMLRenderer()
        md = mistune.create_markdown(renderer=renderer, plugins=[strikethrough, table])
        result = md(text)
        result = _EXCESS_NEWLINES.sub("\n\n", result)
        return result.strip()
    except Exception:
        # If mistune fails on malformed markdown, return escaped plain text
        return html.escape(text).strip()
