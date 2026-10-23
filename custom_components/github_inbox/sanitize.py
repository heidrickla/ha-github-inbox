"""Allowlist sanitiser for the HTML GitHub renders for issue, pull request and comment bodies.

The panel inserts the result as HTML, so this is the boundary. Output is rebuilt from parsed tokens: only the tags and
attributes below are emitted, every text run is escaped again, links open in a new tab, and images and videos become
links so nothing loads from outside Home Assistant.
"""

from __future__ import annotations

import re
from html import escape
from html.parser import HTMLParser
from typing import override

ALLOWED = frozenset(
    {
        "a",
        "b",
        "blockquote",
        "br",
        "code",
        "dd",
        "del",
        "details",
        "div",
        "dl",
        "dt",
        "em",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "hr",
        "i",
        "ins",
        "kbd",
        "li",
        "ol",
        "p",
        "pre",
        "s",
        "span",
        "strong",
        "sub",
        "summary",
        "sup",
        "table",
        "tbody",
        "td",
        "th",
        "thead",
        "tr",
        "ul",
    }
)
VOID = frozenset({"br", "hr"})
# Dropped with everything inside them.
DROP = frozenset(
    {
        "applet",
        "audio",
        "base",
        "button",
        "canvas",
        "embed",
        "form",
        "frame",
        "frameset",
        "head",
        "iframe",
        "link",
        "math",
        "meta",
        "noembed",
        "noframes",
        "noscript",
        "object",
        "script",
        "select",
        "style",
        "svg",
        "template",
        "textarea",
        "title",
        "xmp",
    }
)
MEDIA = {"img": "image", "video": "video"}
LINK_ATTRS = ' target="_blank" rel="noopener noreferrer nofollow"'
MAX_INPUT = 400_000

_CONTROL_RE = re.compile(r"[\x00-\x20\x7f]")
_SPACE_RE = re.compile(r"\s+")


def safe_url(value: str | None) -> str | None:
    """The URL if it is absolute http, https or mailto; browsers ignore control characters inside a scheme."""
    if not value:
        return None
    url = _CONTROL_RE.sub("", value)
    if url.lower().startswith(("https://", "http://", "mailto:")):
        return url
    return None


class _Sanitiser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.open: list[str] = []
        self.skip: list[str] = []

    @override
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.skip:
            if tag == self.skip[-1]:
                self.skip.append(tag)
            return
        if tag in DROP:
            self.skip.append(tag)
            return
        self._emit_start(tag, dict(attrs))

    @override
    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.skip or tag in DROP:
            return
        self._emit_start(tag, dict(attrs), closed=True)

    def _emit_start(
        self, tag: str, attrs: dict[str, str | None], closed: bool = False
    ) -> None:
        if tag in MEDIA:
            self._media(tag, attrs)
            return
        if tag == "input":
            if (attrs.get("type") or "").lower() == "checkbox":
                checked = " checked" if "checked" in attrs else ""
                self.out.append(f'<input type="checkbox" disabled{checked}>')
            return
        if tag not in ALLOWED:
            return
        extra = ""
        if tag == "a":
            href = safe_url(attrs.get("href"))
            if href is None or "a" in self.open:
                return
            extra = f' href="{escape(href)}"{LINK_ATTRS}'
        elif tag == "ol":
            start = attrs.get("start") or ""
            if start.isdigit():
                extra = f' start="{int(start)}"'
        elif tag in ("td", "th"):
            align = (attrs.get("align") or "").lower()
            if align in ("left", "center", "right"):
                extra = f' align="{align}"'
        self.out.append(f"<{tag}{extra}>")
        if tag not in VOID and not closed:
            self.open.append(tag)
        elif tag not in VOID:
            self.out.append(f"</{tag}>")

    def _media(self, tag: str, attrs: dict[str, str | None]) -> None:
        label = f"{MEDIA[tag]}: {attrs.get('alt') or attrs.get('title') or ''}".strip()
        label = label.rstrip(":")
        src = safe_url(attrs.get("src"))
        if src is None or "a" in self.open:
            self.out.append(escape(f"[{label}]", quote=False))
            return
        self.out.append(f'<a href="{escape(src)}"{LINK_ATTRS}>{escape(label)}</a>')

    @override
    def handle_endtag(self, tag: str) -> None:
        if self.skip:
            if tag == self.skip[-1]:
                self.skip.pop()
            return
        if tag not in self.open:
            return
        while self.open:
            last = self.open.pop()
            self.out.append(f"</{last}>")
            if last == tag:
                break

    @override
    def handle_data(self, data: str) -> None:
        if not self.skip:
            self.out.append(escape(data, quote=False))

    def result(self) -> str:
        self.close()
        while self.open:
            self.out.append(f"</{self.open.pop()}>")
        return "".join(self.out)


def sanitize(html: str | None) -> str:
    """Safe HTML for the panel from GitHub-rendered HTML."""
    if not html:
        return ""
    parser = _Sanitiser()
    parser.feed(html[:MAX_INPUT])
    return parser.result()


def text_html(text: str | None) -> str:
    """Plain text (an unrendered Markdown body) as HTML, until GitHub's rendering is fetched."""
    if not text:
        return ""
    paragraphs = [p for p in re.split(r"\n\s*\n", text[:MAX_INPUT]) if p.strip()]
    return "".join(
        "<p>" + escape(p.strip(), quote=False).replace("\n", "<br>") + "</p>"
        for p in paragraphs
    )


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0

    @override
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in DROP:
            self.skip += 1
        self.parts.append(" ")

    @override
    def handle_endtag(self, tag: str) -> None:
        if tag in DROP and self.skip:
            self.skip -= 1
        self.parts.append(" ")

    @override
    def handle_data(self, data: str) -> None:
        if not self.skip:
            self.parts.append(data)


def preview(html: str | None, limit: int = 160) -> str:
    """The first words of a body as one line of plain text."""
    if not html:
        return ""
    parser = _Text()
    parser.feed(html[:MAX_INPUT])
    parser.close()
    text = _SPACE_RE.sub(" ", "".join(parser.parts)).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0] or text[:limit]
    return cut.rstrip(" ,.;:") + "…"
