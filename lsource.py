"""Bulgarian legislation through the lsource.net public API.

The corpus is a reconstruction of the State Gazette (Държавен вестник): every act
carries its gazette chain, its status (in force / repealed / not yet in force) and,
where the corpus has a reconstructed history, the text as of a past date. It is not
an official text and lsource says so on every record; the binding version is the one
promulgated in ДВ.

API key: free for every registered user (https://lsource.net/developers), sent as
``Authorization: Bearer``. The public HTML page of an act is a free preview that
stops mid-sentence, so the API is the only complete machine-readable source and the
plugin refuses to serve a truncated act as if it were whole.
"""

from __future__ import annotations

import json
import os
import re
import time
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

from .client import LawError, Response, Store
from .version import PARSE_VERSION

API = "https://lsource.net/v1"
_P = f"p{PARSE_VERSION}-"
PAGE = "https://lsource.net/laws/"
DOCS_PAGE = "https://lsource.net/developers"

# Abbreviations Bulgarian lawyers actually write, each one verified against the API
# (the id is what /v1/acts/{id} answers for that act).
ALIASES: Dict[str, str] = {
    "конституция": "konstitutsiya-na-republika-balgariya",
    "конституцията": "konstitutsiya-na-republika-balgariya",
    "кт": "kodeks-na-truda",
    "кодекс на труда": "kodeks-na-truda",
    "нк": "nakazatelen-kodeks",
    "наказателен кодекс": "nakazatelen-kodeks",
    "нпк": "nakazatelno-protsesualen-kodeks",
    "гпк": "grazhdanski-protsesualen-kodeks",
    "апк": "administrativnoprotsesualen-kodeks",
    "ззд": "zakon-za-zadalzheniyata-i-dogovorite",
    "тз": "targovski-zakon",
    "търговски закон": "targovski-zakon",
    "ск": "semeen-kodeks",
    "семеен кодекс": "semeen-kodeks",
    "зн": "zakon-za-nasledstvoto",
    "зс": "zakon-za-sobstvenostta",
    "ксо": "kodeks-za-sotsialno-osiguryavane-zagl-izm-dv-br-67-ot-2003-g",
    "ззо": "zakon-za-zdravnoto-osiguryavane",
    "зддс": "zakon-za-danak-varhu-dobavenata-stoynost",
    "допк": "danachno-osiguritelen-protsesualen-kodeks",
    "зут": "zakon-za-ustroystvo-na-teritoriyata",
    "ззлд": "zakon-za-zashtita-na-lichnite-danni",
    "ззк": "zakon-za-zashtita-na-konkurentsiyata",
    "зоп": "zakon-za-obshtestvenite-porachki",
    "занн": "zakon-za-administrativnite-narusheniya-i-nakazaniya",
    "зе": "zakon-za-energetikata",
    "змвр": "zakon-za-ministerstvoto-na-vatreshnite-raboti",
    "ззп": "zakon-za-zashtita-na-potrebitelite",
    "кз": "kodeks-za-zastrahovaneto",
    "зздн": "zakon-za-zashtita-ot-domashnoto-nasilie-zagl-izm-dv-br-102-ot-2009-g-v-sila-ot",
}

# Canonical abbreviation per act id, for rendering a citation the way a lawyer writes it.
_ABBR = [
    ("Конституция", "konstitutsiya-na-republika-balgariya"),
    ("КТ", "kodeks-na-truda"),
    ("НК", "nakazatelen-kodeks"),
    ("НПК", "nakazatelno-protsesualen-kodeks"),
    ("ГПК", "grazhdanski-protsesualen-kodeks"),
    ("АПК", "administrativnoprotsesualen-kodeks"),
    ("ЗЗД", "zakon-za-zadalzheniyata-i-dogovorite"),
    ("ТЗ", "targovski-zakon"),
    ("СК", "semeen-kodeks"),
    ("ЗН", "zakon-za-nasledstvoto"),
    ("ЗС", "zakon-za-sobstvenostta"),
    ("КСО", "kodeks-za-sotsialno-osiguryavane-zagl-izm-dv-br-67-ot-2003-g"),
    ("ЗЗО", "zakon-za-zdravnoto-osiguryavane"),
    ("ЗДДС", "zakon-za-danak-varhu-dobavenata-stoynost"),
    ("ДОПК", "danachno-osiguritelen-protsesualen-kodeks"),
    ("ЗУТ", "zakon-za-ustroystvo-na-teritoriyata"),
    ("ЗЗЛД", "zakon-za-zashtita-na-lichnite-danni"),
    ("ЗЗК", "zakon-za-zashtita-na-konkurentsiyata"),
    ("ЗОП", "zakon-za-obshtestvenite-porachki"),
    ("ЗАНН", "zakon-za-administrativnite-narusheniya-i-nakazaniya"),
    ("ЗЕ", "zakon-za-energetikata"),
    ("ЗМВР", "zakon-za-ministerstvoto-na-vatreshnite-raboti"),
    ("ЗЗП", "zakon-za-zashtita-na-potrebitelite"),
    ("КЗ", "kodeks-za-zastrahovaneto"),
    ("ЗЗДн", "zakon-za-zashtita-ot-domashnoto-nasilie-zagl-izm-dv-br-102-ot-2009-g-v-sila-ot"),
]
ABBR_BY_ID: Dict[str, str] = {act_id: abbr for abbr, act_id in _ABBR}
_LOOSE_ALIASES: Dict[str, str] = {}


def _loose_alias(key: str) -> Optional[str]:
    global _LOOSE_ALIASES
    if not _LOOSE_ALIASES:
        _LOOSE_ALIASES = {loose_title(name): act_id for name, act_id in ALIASES.items()}
    return _LOOSE_ALIASES.get(loose_title(key))

MONTHS = {
    "януари": 1, "февруари": 2, "март": 3, "април": 4, "май": 5, "юни": 6, "юли": 7,
    "август": 8, "септември": 9, "октомври": 10, "ноември": 11, "декември": 12,
}

_ART = re.compile(r"^(Чл|§)\s*\.?\s*(\d+[а-яa-z]?)\s*\.?\s*(.*)$", re.S)
_CHAPTER = re.compile(r"^Глава\s+[а-яА-ЯіІ]+\.?$")
_SECTION = re.compile(r"^Раздел\s+[IVXІХLC]+\.?$")
_DV_ITEM = re.compile(
    r"бр\.?\s*(?P<issue>\d+)\s*от\s*(?P<day>\d{1,2})\s+(?P<month>[А-Яа-я]+)\s*(?P<year>\d{4})\s*г?\.?",
    re.I,
)
_DV_ACTION = re.compile(r"(изм\.\s*и\s*доп\.?|изм\.|доп\.|попр\.|обн\.|в\s*сила\s*от)", re.I)
_PARA = re.compile(r"\((\d+[а-яa-z]?)\)")
_DOTTED = re.compile(r"(?m)^(\d{1,3})\.\s")
_HEADING_STOP = re.compile(r"^(Чл\.|§|\d+\.|[а-я]\)|Глава|Раздел|Обн\.|В сила|\(|Изменение|Допълнение)", re.I)


def fold(text: str) -> str:
    """Case- and accent-insensitive form used for matching."""
    text = (text or "").lower().replace("ѝ", "и")
    text = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def strip_title(text: str) -> str:
    """Title without the '(ЗАГЛ. ИЗМ. - ДВ ...)' tail and without punctuation noise."""
    text = fold(text)
    text = re.sub(r"\(загл\..*?\)", " ", text)
    text = re.sub(r"[\u201e\u201c\"'\u2019()\[\],.\u2116/\u2013\u2014-]+", " ", text)
    return " ".join(text.split())


_ARTICLE_END = re.compile(r"(ът|ят|та|то|те)$")
_SOFT_END = re.compile(r"[ая]$")


def words_covered(want: str, title: str) -> bool:
    """True when every significant word of the query appears in the title."""
    return all(word in title for word in want.split() if len(word) > 2)


def loose_title(text: str) -> str:
    """Title key that ignores the Bulgarian definite article: 'Кодекса на труда' == 'Кодекс на труда'."""
    words = strip_title(text).split()
    out = []
    for word in words:
        if _ARTICLE_END.search(word) and len(word) > 5:
            word = _ARTICLE_END.sub("", word)
        elif _SOFT_END.search(word) and len(word) > 5:
            word = _SOFT_END.sub("", word)
        out.append(word)
    return " ".join(out)


def public_url(act_id: str) -> str:
    return PAGE + act_id


def _json(_body: bytes, resp: Response) -> Any:
    try:
        return json.loads(_body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as err:
        raise LawError("bad_response", f"lsource.net sent something that is not JSON for {resp.url}: {err}") from err


def _iso(day: str, month: str, year: str) -> str:
    num = MONTHS.get(fold(month).strip("."), 0)
    if not num:
        return ""
    return f"{int(year):04d}-{num:02d}-{int(day):02d}"


def parse_gazette_chain(text: str) -> List[Dict[str, str]]:
    """[{'action','issue','date'}] for the whole 'Обн. ДВ. бр.26 от 1 Април 1986г., изм. ...' chain.

    Two layouts exist in the corpus: 'изм. ДВ. бр.6 от 22 Януари 1988г.' and, in older acts
    such as the Constitution, 'Обн., ДВ, бр. 56 от 13 Юли 1991 г. ... изм. и доп., бр. 85
    от 26 Септември 2003 г.' where ДВ is named once and later items carry only the issue.
    The action word is therefore taken from the text just before each issue."""
    raw = text or ""
    rows: List[Dict[str, str]] = []
    last_end = 0
    for m in _DV_ITEM.finditer(raw):
        window = raw[max(last_end, m.start() - 80):m.start()]
        actions = _DV_ACTION.findall(window)
        action = " ".join(actions[-1].split()) if actions else ""
        if not action and not rows:
            action = "обн."
        date = _iso(m.group("day"), m.group("month"), m.group("year"))
        if not date:
            last_end = m.end()
            continue
        rows.append({"action": action.rstrip(".,") or "изм.", "issue": f"бр. {int(m.group('issue'))}/{m.group('year')}",
                     "date": date})
        last_end = m.end()
    # The chain in the source is mostly chronological but a few acts carry an out-of-order
    # entry; a stable sort keeps the reading order and makes the last row the latest issue.
    rows.sort(key=lambda row: row["date"])
    unique: List[Dict[str, str]] = []
    for row in rows:
        if unique and (unique[-1]["issue"], unique[-1]["date"]) == (row["issue"], row["date"]):
            continue
        unique.append(row)
    return unique


def _run(text: str, rx) -> List[tuple]:
    """Markers of one family, reduced to an increasing run that starts at the first number."""
    marks = [(m.start(), m.group(1)) for m in rx.finditer(text or "")]
    if not marks:
        return []
    run = [marks[0]]
    expected = marks[0][1]
    for pos, num in marks[1:]:
        if re.fullmatch(r"\d+", expected or ""):
            want = str(int(expected) + 1)
        else:
            want = expected
        if num == want:
            run.append((pos, num))
            expected = num
    return run


def split_paragraphs(text: str) -> List[Dict[str, str]]:
    """[(number, text)] for the numbered paragraphs of one provision, in source order.

    A Bulgarian national act marks an алинея as '(1) ...'; the Bulgarian and English editions
    of an EU act mark a параграф as a line starting '1. '. Both families are tried and the one
    whose first marker comes earliest wins, so a later cross-reference like 'чл. 6, ал. 1'
    inside the text can never be mistaken for the start of the provision."""
    paren = _run(text, _PARA)
    dotted = _run(text, _DOTTED)
    if paren and dotted:
        marks = paren if paren[0][0] <= dotted[0][0] else dotted
    else:
        marks = paren or dotted
    if not marks:
        return []
    out: List[Dict[str, str]] = []
    for i, part in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        out.append({"number": part[1], "text": text[part[0]:end].strip()})
    return out


def parse_act_text(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Structure one /text payload into headings, chapters, articles and the gazette chain."""
    raw = doc.get("text") or ""
    lines = raw.split("\n")
    meta: Dict[str, Any] = {
        "id": doc.get("id", ""), "title": (doc.get("title") or "").strip(),
        "kind": doc.get("kind", ""), "official": bool(doc.get("official")),
        "notice": doc.get("notice", ""), "at_date": doc.get("at_date") or "",
        "in_force_from": "", "gazette_chain_raw": "", "gazette_chain": [],
    }
    header_done = False
    chapters: List[Dict[str, str]] = []
    sections: List[Dict[str, str]] = []
    articles: List[Dict[str, Any]] = []
    chapter = ""
    section = ""
    current: Optional[Dict[str, Any]] = None
    pending_heading = ""
    header_lines: List[str] = []

    def close() -> None:
        nonlocal current
        if current is not None:
            body = "\n".join(current.pop("_lines")).strip("\n")
            current["text"] = body
            head = body[len(current["ref"]):] if body.startswith(current["ref"]) else body
            current["repealed"] = bool(re.match(r"^[\s.,]*\(?\s*Отм\.", head))
            articles.append(current)
            current = None

    def heading_like(line: str) -> bool:
        text = line.strip()
        if not text or len(text) > 300:
            return False
        if _HEADING_STOP.match(text):
            return False
        return not text.endswith(".")

    for idx, line in enumerate(lines):
        text = line.strip()
        if not text:
            if current is not None:
                current["_lines"].append("")
            continue
        if not header_done:
            header_lines.append(text)
            if _CHAPTER.match(text) or _SECTION.match(text) or _ART.match(text):
                header_done = True
            else:
                if text.startswith("В сила от"):
                    meta["in_force_from"] = text.split("от", 1)[-1].strip().rstrip("г.").strip()
                if "ДВ" in text:
                    meta["gazette_chain_raw"] = (meta["gazette_chain_raw"] + " " + text).strip()
                continue
        if _CHAPTER.match(text):
            close()
            title = ""
            nxt = lines[idx + 1].strip() if idx + 1 < len(lines) else ""
            if nxt and not _SECTION.match(nxt) and not _ART.match(nxt):
                title = nxt
            chapter = f"{text} {title}".strip()
            section = ""
            chapters.append({"heading": text, "title": title, "number": len(chapters) + 1})
            continue
        if _SECTION.match(text):
            close()
            title = ""
            nxt = lines[idx + 1].strip() if idx + 1 < len(lines) else ""
            if nxt and not _ART.match(nxt):
                title = nxt
            section = f"{text} {title}".strip()
            sections.append({"heading": text, "title": title, "number": len(sections) + 1})
            continue
        m = _ART.match(text)
        if m:
            close()
            marker = "Чл." if m.group(1).lower().startswith("чл") else "§"
            ref = f"{marker} {m.group(2)}"
            current = {
                "number": m.group(2), "ref": ref,
                "scope": " / ".join(x for x in (chapter, section) if x),
                "chapter": chapter, "section": section, "heading": pending_heading,
                "_lines": [text],
            }
            pending_heading = ""
            continue
        nxt = ""
        for follow in lines[idx + 1:]:
            if follow.strip():
                nxt = follow.strip()
                break
        if current is not None and nxt and _ART.match(nxt) and heading_like(text):
            close()
            pending_heading = text
            continue
        if current is not None:
            current["_lines"].append(text)
        elif not articles and heading_like(text) and nxt and _ART.match(nxt):
            pending_heading = text
    close()
    meta["gazette_chain"] = parse_gazette_chain(meta["gazette_chain_raw"])
    return {"meta": meta, "chapters": chapters, "sections": sections, "articles": articles,
            "chars": len(raw)}


class BulgarianLaw:
    def __init__(self, store: Store):
        self.store = store

    # -- key ---------------------------------------------------------------
    def key(self) -> str:
        for key in ("lsource_api_key",):
            value = self.store.setting(key, "")
            if isinstance(value, str) and value.strip():
                return value.strip()
        env = os.environ.get("LSOURCE_API_KEY", "").strip()
        if env:
            return env
        raise LawError(
            "key_required",
            "Bulgarian statute text needs a free lsource.net API key, and this plugin has none.",
            "Ask the user for a key from https://lsource.net/developers (free for every registered user) and set it "
            "in this plugin's settings, or read the act in a browser at its public page.",
        )

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.key()}", "Accept": "application/json"}

    def _get(self, key: str, url: str, max_age_s: Optional[float] = None, immutable: bool = False,
             parse: Optional[Any] = None):
        last: Optional[LawError] = None
        parser = parse or _json
        for attempt in range(2):
            try:
                return self.store.fetch(key, url, parser, headers=self._headers(),
                                        max_age_s=max_age_s, immutable=immutable)
            except LawError as err:
                last = err
                if err.kind not in ("upstream", "rate_limited") or attempt:
                    raise
                time.sleep(1.5)
        raise last or LawError("upstream", "lsource.net is unavailable.")

    # -- endpoints ---------------------------------------------------------
    def search(self, query: str, limit: int = 10, kind: str = "") -> List[Dict[str, Any]]:
        params = [f"q={_q(query)}", f"limit={max(1, min(50, limit))}"]
        if kind:
            params.append(f"kind={_q(kind)}")
        got = self._get(f"{_P}bg-search-{fold(query)}-{limit}-{fold(kind)}", f"{API}/acts?{'&'.join(params)}")
        return got.data.get("results", []) if isinstance(got.data, dict) else []

    def meta(self, act_id: str):
        return self._get(f"{_P}bg-meta-{act_id}", f"{API}/acts/{_q(act_id)}")

    def history(self, act_id: str):
        return self._get(f"{_P}bg-history-{act_id}", f"{API}/acts/{_q(act_id)}/history")

    def act(self, act_id: str):
        """Parsed consolidated text of one act (cached; rechecked with the source after max_age_hours)."""
        return self._get(f"{_P}bg-text-{act_id}", f"{API}/acts/{_q(act_id)}/text", parse=lambda body, resp: parse_act_text(_json(body, resp)))

    def article_now(self, act_id: str, number: str):
        return self._get(f"{_P}bg-art-{act_id}-{number}", f"{API}/acts/{_q(act_id)}/articles/{_q(number)}", immutable=True)

    def article_as_of(self, act_id: str, number: str, as_of: str):
        url = f"{API}/acts/{_q(act_id)}/articles/{_q(number)}?as_of={_q(as_of)}"
        return self._get(f"{_P}bg-art-{act_id}-{number}-{as_of}", url, immutable=True)

    # -- resolution --------------------------------------------------------
    def resolve(self, name: str) -> Tuple[str, str]:
        """(act id, title) for an abbreviation, a title, or an id."""
        query = " ".join((name or "").replace("\u00a0", " ").split()).strip(" .,;:")
        if not query:
            raise LawError("bad_argument", "No law given.",
                           "Pass the act, e.g. 'КТ', 'ЗЗД', 'Кодекс на труда' or 'Наказателен кодекс'.")
        key = fold(query).strip()
        if key in ALIASES:
            act_id = ALIASES[key]
            meta = self._safe_meta(act_id)
            return act_id, (meta or {}).get("title", query)
        loose = _loose_alias(query)
        if loose:
            meta = self._safe_meta(loose)
            return loose, (meta or {}).get("title", query)
        if re.fullmatch(r"[a-z0-9][a-z0-9-]{3,}", query):
            meta = self._safe_meta(query)
            if meta:
                return query, meta.get("title", query)
        want = loose_title(query)
        rows = self._find_rows(query, want)
        if rows:
            exact = [r for r in rows if loose_title(r.get("title", "")) == want]
            if len(exact) == 1:
                return exact[0]["id"], exact[0]["title"]
            starts = [r for r in rows if loose_title(r.get("title", "")).startswith(want)]
            if len(starts) == 1:
                return starts[0]["id"], starts[0]["title"]
            # A single search hit is only trusted when it actually covers the query: the API matches
            # titles literally, so a наредба whose title quotes a закон can be the only row returned.
            if len(rows) == 1 and words_covered(want, loose_title(rows[0].get("title", ""))):
                return rows[0]["id"], rows[0]["title"]
            if exact:
                rows = exact
            elif starts:
                rows = starts
            raise LawError("ambiguous_law", f"'{query}' matches {len(rows)} acts.",
                           "Pass one of the listed ids or a longer title.",
                           {"candidates": [{"law": r["id"], "abbreviation": r.get("kind", ""),
                                            "title": r.get("title", ""), "issue": r.get("issue", "")}
                                           for r in rows[:10]]})
        raise LawError("law_not_found", f"No Bulgarian act in the corpus matches '{query}'.",
                       "Call bg_law_search with a shorter title word, or the official abbreviation "
                       "(ALIASES covers the common ones, e.g. КТ, НК, ЗЗД, ТЗ). Municipal наредби and "
                       "EU acts are not in this corpus.")

    def _find_rows(self, query: str, loose: str) -> List[Dict[str, Any]]:
        """Search rows for the query, and for its article-free form when the two differ.

        Titles are matched literally by the API, so a query carrying the definite article
        ('Закона за защита на личните данни') misses the act itself and can land on an unrelated
        act whose title merely quotes that string. The loose form is tried first for that reason."""
        seen: Dict[str, Dict[str, Any]] = {}
        terms = ([loose] if loose and loose != query else []) + [query]
        for term in terms:
            if not term:
                continue
            try:
                found = self.search(term, 10)
            except LawError as err:
                if err.kind == "law_not_found" and term != query:
                    continue
                raise
            for row in found:
                seen.setdefault(row.get("id", ""), row)
        return list(seen.values())

    def _safe_meta(self, act_id: str) -> Optional[Dict[str, Any]]:
        try:
            got = self.meta(act_id)
            return got.data if isinstance(got.data, dict) else None
        except LawError as err:
            if err.kind in ("not_found", "bad_request"):
                return None
            raise


def _q(value: str) -> str:
    from urllib.parse import quote
    return quote(str(value), safe="")


def find_article(parsed: Dict[str, Any], number: str, kind: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The article with this number, in the requested marker family ('article' / 'paragraph_section')."""
    from .citations import cyrillic_letter

    want = (number or "").strip().lower()
    m = re.match(r"^(\d+)([a-zа-я]{0,2})$", want)
    if m:
        want = m.group(1) + (cyrillic_letter(m.group(2)) if m.group(2) else "")
    families = {"article": "Чл.", "paragraph_section": "§"}
    marker = families.get(kind or "")
    for art in parsed["articles"]:
        if marker and not art["ref"].startswith(marker):
            continue
        if art["number"].lower() == want:
            return art
    if marker:
        return None
    for art in parsed["articles"]:
        if art["number"].lower() == want:
            return art
    return None


def neighbours(parsed: Dict[str, Any], number: str, limit: int = 8) -> List[str]:
    """Article numbers that share the digits of a missing number (312z asked, act has 312 to 312k)."""
    digits = re.sub(r"\D", "", number or "")
    if not digits:
        return []
    out = [a["ref"] for a in parsed["articles"] if re.sub(r"\D", "", a["number"]) == digits]
    return out[:limit]
