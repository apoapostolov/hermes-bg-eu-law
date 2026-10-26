"""Turn source markup into plain text without adding or dropping words.

Rules: paragraphs and list items become lines, nested lists are indented by two
spaces, table rows become one line with " | " between cells, line breaks are kept,
footnote reference marks are dropped. Whitespace inside a line is collapsed.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Dict, List, Optional
from xml.etree import ElementTree as ET

_WS = re.compile(r"[ \t\r\f\v\u00a0]+")
_BLOCK = {"p", "div", "dl", "table", "tr", "row", "title", "pre", "li", "ul", "ol", "tbody", "thead",
          "tgroup", "h1", "h2", "h3", "h4", "h5", "h6", "content", "toc", "footnotes", "footnote",
          "hr", "blockquote", "section", "article"}
_SKIP = {"fnr", "colspec", "style", "script", "head", "img"}


class _Out:
    """Collects text into lines. ``indent`` is applied to new lines."""

    def __init__(self):
        self.lines: List[str] = []
        self.cur: List[str] = []
        self.indent = 0
        self.pre = 0

    def text(self, s: Optional[str]) -> None:
        if not s:
            return
        if self.pre:
            parts = s.split("\n")
            for i, part in enumerate(parts):
                if i:
                    self.newline()
                self.cur.append(part)
            return
        self.cur.append(_WS.sub(" ", s.replace("\n", " ")))

    def newline(self) -> None:
        line = "".join(self.cur)
        line = line.rstrip() if self.pre else _WS.sub(" ", line).strip()
        if line:
            self.lines.append("  " * self.indent + line)
        self.cur = []

    def result(self) -> str:
        self.newline()
        text = "\n".join(self.lines)
        return re.sub(r"\n{3,}", "\n\n", text).strip()


def _tag(el) -> str:
    tag = el.tag if isinstance(el.tag, str) else ""
    return tag.rsplit("}", 1)[-1].lower()


def xml_to_text(el: Optional[ET.Element]) -> str:
    """Plain text of a gesetze-im-internet / rechtsprechung-im-internet XML fragment."""
    if el is None:
        return ""
    out = _Out()
    _walk_xml(el, out)
    return out.result()


def _walk_xml(el: ET.Element, out: _Out) -> None:
    tag = _tag(el)
    if tag in _SKIP:
        out.text(el.tail)
        return
    if tag == "br":
        out.newline()
        out.text(el.tail)
        return
    if tag == "dl":
        out.newline()
        _walk_dl(el, out)
        out.newline()
        out.text(el.tail)
        return
    if tag in ("row", "tr"):
        out.newline()
        cells = [xml_to_text(c).replace("\n", " ") for c in el if _tag(c) in ("entry", "td", "th")]
        out.text(join_cells(cells))
        out.newline()
        out.text(el.tail)
        return
    if tag == "pre":
        out.newline()
        out.pre += 1
    elif tag in _BLOCK:
        out.newline()
    out.text(el.text)
    for child in el:
        _walk_xml(child, out)
    if tag == "pre":
        out.newline()
        out.pre -= 1
    elif tag in _BLOCK:
        out.newline()
    out.text(el.tail)


def _walk_dl(el: ET.Element, out: _Out) -> None:
    """DT/DD pairs: '1. text', nested lists indented."""
    label = ""
    for child in el:
        t = _tag(child)
        if t == "dt":
            label = xml_to_text(child).replace("\n", " ").strip()
        elif t == "dd":
            body = xml_to_text(child)
            lines = body.split("\n") if body else [""]
            first = (label + " " + lines[0]).strip()
            if first:
                out.lines.append("  " * out.indent + first)
            for extra in lines[1:]:
                if extra.strip():
                    out.lines.append("  " * (out.indent + 1) + extra)
            label = ""
        else:
            _walk_xml(child, out)
    if label:
        out.lines.append("  " * out.indent + label)


# ---------------------------------------------------------------------------
# Minimal HTML tree for the EU XHTML (CELLAR). Stdlib only.

class Node:
    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag: str, attrs: Dict[str, str], parent: Optional["Node"] = None):
        self.tag = tag
        self.attrs = attrs
        self.children: list = []  # Node or str
        self.parent = parent

    @property
    def cls(self) -> str:
        return self.attrs.get("class", "") or ""


_VOID = {"br", "hr", "img", "meta", "link", "col", "input", "wbr"}


class _TreeBuilder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#root", {})
        self.cur = self.root
        self.by_id: Dict[str, Node] = {}

    def handle_starttag(self, tag, attrs):
        node = Node(tag, {k: (v or "") for k, v in attrs}, self.cur)
        self.cur.children.append(node)
        if node.attrs.get("id"):
            self.by_id.setdefault(node.attrs["id"], node)
        if tag not in _VOID:
            self.cur = node

    def handle_startendtag(self, tag, attrs):
        node = Node(tag, {k: (v or "") for k, v in attrs}, self.cur)
        self.cur.children.append(node)
        if node.attrs.get("id"):
            self.by_id.setdefault(node.attrs["id"], node)

    def handle_endtag(self, tag):
        node = self.cur
        while node is not None and node.tag != tag:
            node = node.parent
        if node is not None and node.parent is not None:
            self.cur = node.parent

    def handle_data(self, data):
        self.cur.children.append(data)


def parse_html(text: str):
    builder = _TreeBuilder()
    builder.feed(text)
    builder.close()
    return builder.root, builder.by_id


_INLINE_LABEL = re.compile(r"^(\(\d+[a-z]?\)|\(?[a-z]{1,2}\)|\d+[a-z]?\.|\([ivxlc]+\)|[ivxlc]+\)|[-\u2013\u2014\u2022])$")


def join_cells(cells: List[str]) -> str:
    """Table row as one line; a leading label cell ('(1)', 'a)') is joined with a space."""
    cells = [c.strip() for c in cells if c and c.strip()]
    if len(cells) >= 2 and _INLINE_LABEL.match(cells[0]):
        return cells[0] + " " + " | ".join(cells[1:])
    return " | ".join(cells)


def html_to_text(node: Node) -> str:
    out = _Out()
    _walk_html(node, out)
    lines = out.result().split("\n")
    merged: List[str] = []
    for line in lines:
        # A list label rendered as its own block ("a)", "(1)") belongs to the next line.
        if merged and _INLINE_LABEL.match(merged[-1].strip()):
            prev = merged.pop()
            indent = len(prev) - len(prev.lstrip())
            merged.append(" " * indent + prev.strip() + " " + line.strip())
        else:
            merged.append(line)
    return "\n".join(merged).strip()


def _walk_html(node, out: _Out) -> None:
    if isinstance(node, str):
        out.text(node)
        return
    tag = node.tag
    if tag in _SKIP:
        return
    if tag == "br":
        out.newline()
        return
    if tag == "tr":
        out.newline()
        cells = [html_to_text(c).replace("\n", " ") for c in node.children
                 if isinstance(c, Node) and c.tag in ("td", "th")]
        out.text(join_cells(cells))
        out.newline()
        return
    block = tag in _BLOCK
    if block:
        out.newline()
    for child in node.children:
        _walk_html(child, out)
    if block:
        out.newline()
