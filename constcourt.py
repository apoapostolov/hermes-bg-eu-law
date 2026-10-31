"""Acts of the Bulgarian Constitutional Court (www.constcourt.bg).

The site publishes every act twice: once in a searchable list (act type, date, case
number, reporting judge, links) and once as a full-text page. The list is one
document, so the plugin caches it and filters locally; the text of an act is fetched
only when the model asks for it, or when a keyword search has to scan candidates.

Robots: constcourt.bg allows every path except /control/.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from .client import LawError, Response, Store
from .version import PARSE_VERSION
from .lsource import fold
from .textutil import html_to_text, parse_html

BASE = "https://www.constcourt.bg"
LIST_URL = f"{BASE}/bg/search-acts"
ROW_URL = f"{BASE}/bg/act-{{id}}"
CASE_URL = f"{BASE}/bg/case-{{id}}"
INDEX_MAX_AGE_S = 7 * 24 * 3600

_ROW = re.compile(r'<div class="result-item">(.*?)(?=<div class="result-item">|</div>\s*</div>\s*</section>)', re.S)
_DATE = re.compile(r"(\d{2})[.\-](\d{2})[.\-](\d{4})")
_CASE = re.compile(r"по\s*к\.?\s*д\.?\s*№\s*(\d+)\s*/\s*(\d{4})")
_ACT_ID = re.compile(r"/bg/act-(\d+)")
_CASE_ID = re.compile(r"/bg/case-(\d+)")
_TYPE = re.compile(r"<h3>(.*?)</h3>", re.S)
_JUDGE = re.compile(r'class="small-title">(.*?)</span>', re.S)


def _clean(text: str) -> str:
    import html as _html
    return " ".join(_html.unescape(re.sub(r"<[^>]+>", " ", text or "")).split())


def _parse_index(body: bytes, resp: Response) -> List[Dict[str, str]]:
    raw = body.decode("utf-8", errors="replace")
    rows: List[Dict[str, str]] = []
    for block in _ROW.findall(raw):
        act = _ACT_ID.search(block)
        date = _DATE.search(block)
        case = _CASE.search(_clean(block)) or _CASE.search(block)
        if not act or not date:
            continue
        act_type = _clean((_TYPE.search(block) or [None, ""])[1]) if _TYPE.search(block) else ""
        judges = _clean((_JUDGE.search(block) or [None, ""])[1]) if _JUDGE.search(block) else ""
        case_id = _CASE_ID.search(block)
        rows.append({
            "doc_id": f"act-{act.group(1)}",
            "act_type": act_type,
            "date": f"{date.group(3)}-{date.group(2)}-{date.group(1)}",
            "case_number": case.group(1) if case else "",
            "case_year": case.group(2) if case else "",
            "judges": judges,
            "url": ROW_URL.format(id=act.group(1)),
            "case_url": CASE_URL.format(id=case_id.group(1)) if case_id else "",
        })
    if not rows:
        raise LawError("bad_response", "The Constitutional Court index contained no acts.")
    return rows


def _parse_act(body: bytes, resp: Response) -> Dict[str, Any]:
    raw = body.decode("utf-8", errors="replace")
    root, _ids = parse_html(raw)
    meta: Dict[str, str] = {}
    for m in re.finditer(
            r'case-detail-small-info">(.*?)</div>\s*<div class="case-detail-big-info">(.*?)</div>', raw, re.S):
        label = _clean(m.group(1)).lower()
        value = _clean(m.group(2))
        if label in ("вид на акта", "дата", "към дело") and label not in meta:
            meta[label] = value
    heading = re.search(r"<h1[^>]*>(.*?)</h1>", raw, re.S)
    body_start = raw.find('id="document-content"')
    text = ""
    if body_start > 0:
        open_end = raw.find(">", body_start)
        container = raw[open_end + 1:] if open_end > 0 else raw[body_start:]
        end = container.find('class="follow-cta"')
        text = html_to_text(parse_html(container[:end if end > 0 else len(container)])[0])
    if not text:
        text = html_to_text(root)
    text = text.strip()
    if not meta.get("вид на акта") or len(text) < 100:
        # An unknown or withdrawn act still answers 200, with the site chrome and no case detail block.
        raise LawError("case_not_found", f"No published Constitutional Court act at {resp.url}.",
                       "Pass a doc_id from bg_case_search; the act may be unpublished or withdrawn.")
    date = meta.get("дата", "")
    d = _DATE.search(date or "")
    return {
        "doc_id": (re.search(r"/act-(\d+)", resp.url).group(1) if re.search(r"/act-(\d+)", resp.url) else ""),
        "act_type": (meta.get("вид на акта") or _clean(heading.group(1))) if heading else meta.get("вид на акта", ""),
        "date": f"{d.group(3)}-{d.group(2)}-{d.group(1)}" if d else date,
        "case_number": meta.get("към дело", ""),
        "text": text,
        "url": resp.url,
    }


class CaseLaw:
    def __init__(self, store: Store):
        self.store = store

    def index(self):
        return self.store.fetch(f"p{PARSE_VERSION}-cc-index", LIST_URL, _parse_index, max_age_s=INDEX_MAX_AGE_S)

    def rows(self) -> List[Dict[str, str]]:
        return list(self.index().data)

    def search(self, case_number: Optional[str] = None, date_from: Optional[str] = None,
               date_to: Optional[str] = None, act_type: Optional[str] = None,
               query: Optional[str] = None, scan_limit: int = 8) -> Tuple[List[Dict[str, str]], int, bool]:
        """(rows, scanned, capped). A keyword search scans up to ``scan_limit`` acts, newest first."""
        rows = self.rows()
        num = re.sub(r"[^\d/]", "", case_number or "")
        want_num, want_year = "", ""
        if num:
            parts = [p for p in num.split("/") if p]
            want_num = parts[0] if parts else ""
            want_year = parts[1] if len(parts) > 1 else ""
        kind = fold(act_type or "")
        filtered = []
        for row in rows:
            if want_num and row["case_number"] != want_num:
                continue
            if want_year and row["case_year"] != want_year:
                continue
            if kind and fold(row["act_type"]) != kind:
                continue
            if date_from and row["date"] < date_from:
                continue
            if date_to and row["date"] > date_to:
                continue
            filtered.append(row)
        filtered.sort(key=lambda r: r["date"], reverse=True)
        if not query:
            return filtered, 0, False
        needle = fold(query)
        hits: List[Dict[str, str]] = []
        scanned = 0
        capped = False
        for row in filtered:
            if scanned >= max(1, scan_limit):
                capped = True
                break
            try:
                act = self.act(row["doc_id"])
            except LawError:
                continue
            scanned += 1
            text = act["text"]
            pos = fold(text).find(needle)
            if pos < 0:
                continue
            row = dict(row, snippet=_snippet(text, pos, len(query)))
            hits.append(row)
        return hits, scanned, capped

    def act(self, doc_id: str) -> Dict[str, Any]:
        doc_id = (doc_id or "").strip()
        m = re.fullmatch(r"(?:act-)?(\d+)", doc_id)
        if not m:
            raise LawError("bad_argument", f"'{doc_id}' is not a Constitutional Court act id.",
                           "Pass doc_id from bg_case_search, for example 'act-10457'.")
        return self.store.fetch(f"p{PARSE_VERSION}-cc-act-{m.group(1)}", ROW_URL.format(id=m.group(1)), _parse_act,
                                max_age_s=30 * 24 * 3600).data


def _snippet(text: str, pos: int, size: int) -> str:
    start = max(0, pos - 120)
    end = min(len(text), pos + size + 180)
    return ("…" if start else "") + " ".join(text[start:end].split()) + "…"
