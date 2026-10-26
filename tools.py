"""Tool handlers. Every handler returns a JSON string and never raises: bad arguments,
source outages and bugs come back as {"ok": false, "error", "error_type", "next_step"}."""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Callable, Dict, List, Optional

from . import constcourt, eu as eu_mod, lsource
from .citations import cyrillic_letter, parse_citation, split_section
from .client import LawError, Store

logger = logging.getLogger(__name__)

DEFAULT_MAX_CHARS = 6000
_PARA = re.compile(r"^\((\d+[a-zа-я]?)\)\s")

# Abbreviation per act id, for rendering a citation the way a lawyer writes it.
ABBR_BY_ID = lsource.ABBR_BY_ID


# ---------------------------------------------------------------- helpers

def _dump(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _error(kind: str, message: str, hint: str = "", details: Optional[dict] = None) -> str:
    payload: Dict[str, Any] = {"ok": False, "error": message, "error_type": kind}
    for key, value in (details or {}).items():
        if value not in (None, "", []):
            payload[key] = value
    if hint:
        payload["next_step"] = hint
    return _dump(payload)


def _int(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        num = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, num))


def _day(lang: str, ts: float) -> str:
    """Date the text was fetched from (or last confirmed by) the source."""
    return time.strftime("%d.%m.%Y", time.gmtime(ts or time.time()))


def _cut(text: str, max_chars: int) -> Dict[str, Any]:
    if len(text) <= max_chars:
        return {"text": text, "truncated": False}
    cut = text.rfind("\n", max_chars // 2, max_chars)
    cut = cut if cut > 0 else max_chars
    return {"text": text[:cut].rstrip(), "truncated": True, "total_chars": len(text)}


def _iso_ts(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts)) if ts else ""


def _cite_bg(art: Dict[str, Any], parsed: Dict[str, Any], title: str, abbr: str = "") -> str:
    number = art["number"]
    ref = f"чл. {number}"
    if not art["ref"].startswith("Чл"):
        ref = f"§ {number}"
    if parsed.get("paragraph"):
        ref += f", ал. {parsed['paragraph']}"
    if parsed.get("point"):
        ref += f", т. {parsed['point']}"
    if parsed.get("letter"):
        ref += f", б. „{cyrillic_letter(parsed['letter'])}“"
    # A short all-caps abbreviation is how a lawyer writes it; a long name reads as 'от <title>'.
    if abbr and abbr.isupper() and len(abbr) <= 6:
        return f"{ref} {abbr}"
    return f"{ref} от {title}"


class Tools:
    def __init__(self, store: Optional[Store] = None):
        self.store = store or Store()
        self.bg = lsource.BulgarianLaw(self.store)
        self.eu = eu_mod.EuLaw(self.store)
        self.cases = constcourt.CaseLaw(self.store)

    # -- settings ----------------------------------------------------------
    def _lang(self, source_lang: str) -> str:
        pref = str(self.store.setting("language", "auto") or "auto").lower()
        return pref if pref in ("bg", "en") else source_lang

    def _flag(self, key: str) -> bool:
        value = self.store.setting(key, True)
        if isinstance(value, str):
            return value.strip().lower() not in ("false", "0", "no", "off")
        return bool(value)

    def _eu_lang(self, requested: Any) -> str:
        lang = str(requested or self.store.setting("eu_language", "bg") or "bg").lower()[:2]
        return lang if lang in ("bg", "en") else "bg"

    # -- sources -----------------------------------------------------------
    def _source_bg(self, act_id: str, title: str, meta: Dict[str, Any], cached) -> Dict[str, Any]:
        lang = self._lang("bg")
        day = _day(lang, cached.fetched_at)
        issue = meta.get("issue", "")
        changed = meta.get("changed", "")
        if lang == "bg":
            attribution = (f"Източник: Питай законите (lsource.net), консолидиран текст, реконструиран от "
                           f"Държавен вестник, лиценз CC BY 4.0. {title}, {issue} ({changed}), изтеглено на "
                           f"{day}; извадка, създадена с hermes-bg-law. Текстът не е официален: обвързва "
                           f"обнародваната в Държавен вестник редакция (dv.parliament.bg).")
        else:
            attribution = (f"Source: Питай законите (lsource.net), consolidated text reconstructed from the "
                           f"Bulgarian State Gazette, CC BY 4.0. {title}, {issue} ({changed}), retrieved {day}; "
                           f"excerpt made with hermes-bg-law. This is not an official text: only the version "
                           f"promulgated in the State Gazette (dv.parliament.bg) is binding.")
        return {"name": "Питай законите (lsource.net)", "url": lsource.public_url(act_id),
                "data_url": f"{lsource.API}/acts/{act_id}", "gazette": "https://dv.parliament.bg/",
                "retrieved_at": _iso_ts(cached.fetched_at), "cache": cached.state,
                "license": "CC BY 4.0", "attribution": attribution}

    def _source_eu(self, doc_id: str, consolidated: Optional[str], text_lang: str, cached) -> Dict[str, Any]:
        lang = self._lang(text_lang)
        if lang == "bg":
            version = (f"Консолидирана редакция от {consolidated}" if consolidated
                       else "Текст, както е публикуван в Официален вестник")
            attribution = (f"Източник: Служба за публикации на Европейския съюз (publications.europa.eu, данни от "
                           f"EUR-Lex), © Европейски съюз, CC BY 4.0. {version} (CELEX {doc_id}), изтеглено на "
                           f"{_day(lang, cached.fetched_at)}; извадка и текстово форматиране с hermes-bg-law. "
                           f"Обвързващ е текстът, публикуван в Официален вестник на Европейския съюз.")
        else:
            version = (f"Consolidated version of {consolidated}" if consolidated
                       else "Text as published in the Official Journal")
            attribution = (f"Source: Publications Office of the European Union (publications.europa.eu, EUR-Lex "
                           f"data), © European Union, CC BY 4.0. {version} (CELEX {doc_id}), retrieved "
                           f"{_day(lang, cached.fetched_at)}; excerpt and plain-text formatting by hermes-bg-law. "
                           f"Only the Official Journal of the European Union is authentic.")
        return {"name": "Publications Office of the EU (CELLAR)", "url": eu_mod.human_url(doc_id, text_lang),
                "data_url": eu_mod.CELLAR + doc_id, "retrieved_at": _iso_ts(cached.fetched_at),
                "cache": cached.state, "license": "CC BY 4.0", "attribution": attribution}

    @staticmethod
    def _stale_note(cached) -> Optional[str]:
        if cached.state == "stale":
            return (f"The source could not be reached; this is the copy cached on {_iso_ts(cached.fetched_at)}. "
                    "Say so when you quote it.")
        return None

    # ------------------------------------------------------------- bg_law_get
    def law_get(self, args: Dict[str, Any]) -> Dict[str, Any]:
        citation = str(args.get("citation") or "").strip()
        parsed = parse_citation(citation) if citation else {}
        if args.get("law"):
            parsed["law"] = str(args["law"]).strip()
        if args.get("article") not in (None, ""):
            parsed.update({k: v for k, v in split_section(args["article"]).items() if v})
        if args.get("paragraph") not in (None, ""):
            parsed["paragraph"] = str(args["paragraph"]).strip().strip("()").lower()
        law = parsed.get("law")
        if not law:
            raise LawError("bad_argument", "No act given.",
                           "Pass a citation like 'чл. 344 КТ' or 'чл. 6 ОРЗД', or law plus article.")
        if not parsed.get("number") and not parsed.get("label"):
            raise LawError("bad_argument", f"No article given for {law}.",
                           "Add the number (e.g. 'чл. 344'), or call bg_law_toc to list the articles.")
        max_chars = _int(args.get("max_chars"), DEFAULT_MAX_CHARS, 500, 30000)
        act = eu_mod.match_eu(law)
        if act:
            if not self._flag("eu_law"):
                raise LawError("disabled", "EU law lookups are switched off in this plugin's settings (eu_law).")
            return self._eu_get(act, parsed, self._eu_lang(args.get("language")), max_chars)
        return self._bg_get(law, parsed, max_chars, args.get("as_of"))

    def _bg_get(self, law: str, parsed: Dict[str, Any], max_chars: int,
                as_of: Any = None) -> Dict[str, Any]:
        as_of = str(as_of or "").strip()
        if as_of and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", as_of):
            raise LawError("bad_argument", f"'{as_of}' is not a date.", "Use YYYY-MM-DD.")
        act_id, title = self.bg.resolve(law)
        try:
            meta = self.bg.meta(act_id)
        except LawError as err:
            if err.kind == "not_found":
                raise LawError("law_not_found", f"No Bulgarian act with id '{act_id}'.") from err
            raise
        cached = self.bg.act(act_id)
        data = cached.data
        act_title = data["meta"].get("title") or title
        abbr = ABBR_BY_ID.get(act_id, "")
        number = parsed.get("number") or ""
        art = lsource.find_article(data, number, parsed.get("kind"))
        if art is None:
            marker = "§" if parsed.get("kind") == "paragraph_section" else "чл."
            raise LawError("article_not_found", f"{act_title} has no {marker} {number}.",
                           "Do not guess a nearby number. Call bg_law_toc to list the articles of this act.",
                           {"law": act_id, "same_number_other_letter": lsource.neighbours(data, number)})
        result: Dict[str, Any] = {"ok": True, "jurisdiction": "BG"}
        text = art["text"]
        if as_of:
            try:
                got = self.bg.article_as_of(act_id, art["number"], as_of)
                text = got.data.get("text") or text
                result["as_of"] = as_of
                result["as_of_note"] = ("Text as reconstructed for the date asked. Where the corpus has no "
                                        "reconstructed history that far back, this is the current text.")
            except LawError as err:
                if err.kind == "not_found":
                    raise LawError("as_of_not_available",
                                   f"No reconstructed text of {act_id} exists for {as_of}.",
                                   "Read the current text without as_of, and say the corpus has no "
                                   "point-in-time text for that date.",
                                   {"history": (self.bg._safe_meta(act_id) or {}).get("history", {})}) from err
                raise
        para = parsed.get("paragraph")
        if para:
            parts = lsource.split_paragraphs(text)
            hit = next((p["text"] for p in parts if p["number"] == para), None)
            if hit is None:
                if not parts and para == "1":
                    result["paragraph_note"] = "This provision has no numbered paragraphs; the whole text is returned."
                    hit = text
                else:
                    raise LawError("paragraph_not_found", f"алинея ({para}) does not exist in чл. {art['number']}.",
                                   "Read it without paragraph, or pick one of the listed алинеи.",
                                   {"paragraphs": [p["number"] for p in parts]})
            text = hit
            result["paragraph"] = para
        pins = {k: parsed[k] for k in ("point", "letter") if parsed.get(k)}
        if pins:
            result["pinpoint"] = pins
            result["pinpoint_note"] = ("The text returned is the whole алинея; the точка or буква asked for is "
                                       "inside it. Quote it from this text.")
        cut = _cut(text, max_chars)
        provision: Dict[str, Any] = {
            "citation": _cite_bg(art, parsed, act_title, abbr),
            "id": art["ref"], "heading": art["heading"], "scope": art["scope"], **cut,
        }
        if art.get("repealed"):
            provision["repealed"] = True
            provision["note"] = "This provision is repealed (отменен) in the current text."
        if cut["truncated"]:
            provision["next_step"] = f"Text cut at {max_chars} characters; call again with max_chars up to 30000."
        chain = data["meta"].get("gazette_chain") or []
        latest = chain[-1] if chain else None
        issue = meta.data.get("issue") or (latest or {}).get("issue", "")
        changed = meta.data.get("changed") or (latest or {}).get("date", "")
        action = meta.data.get("action") or (latest or {}).get("action", "")
        result.update({
            "law": {"id": act_id, "title": act_title, "abbreviation": abbr,
                    "kind": data["meta"].get("kind", ""), "url": lsource.public_url(act_id)},
            "provision": provision,
            "version": {"status": meta.data.get("status", ""), "issue": issue,
                        "changed": changed, "action": action,
                        "in_force_from": data["meta"].get("in_force_from", ""),
                        "official": False, "audit_score": meta.data.get("audit_score"),
                        "gazette_latest": latest,
                        "gazette_count": len(chain),
                        "note": ("The status and the gazette chain belong to the whole act, not only to this "
                                 "provision. lsource.net is a reconstruction: the ДВ text is binding.")},
            "source": self._source_bg(act_id, act_title, {"issue": issue, "changed": changed}, cached),
        })
        stale = self._stale_note(cached)
        if stale:
            result["stale"] = True
            result["stale_note"] = stale
        return result

    def _eu_get(self, act: Dict[str, Any], parsed: Dict[str, Any], lang: str, max_chars: int) -> Dict[str, Any]:
        celex = act["celex"]
        kind = parsed.get("kind") or "article"
        if kind == "section":
            kind = "article"
        number = parsed.get("number") or ""
        uid = eu_mod.unit_id(kind, number)
        if not uid:
            raise LawError("bad_argument", f"'{number}' is not a valid {kind} number.")
        cached, doc_id, consolidated, note = self._eu_doc(act, lang, original=kind == "recital")
        units = cached.data["units"]
        unit = units.get(uid)
        if unit is None and kind == "annex" and not number.isdigit():
            unit = units.get(uid.upper())
        if unit is None:
            have = sum(1 for k in units if k.startswith(uid.split("_")[0] + "_"))
            raise LawError("article_not_found", f"{act.get(lang) or celex} has no {kind} {number} in {doc_id}.",
                           f"The document has {have} {kind} units. Do not guess; call bg_law_toc for the list.",
                           {"celex": celex})
        result: Dict[str, Any] = {"ok": True, "jurisdiction": "EU"}
        text = unit["text"]
        lines = text.split("\n")
        heading: List[str] = []
        if kind == "article" and lines and re.match(r"^(Член|Artikel|Article)\s+\d", lines[0]):
            heading = lines[:2] if len(lines) > 1 and not _PARA.match(lines[1]) else lines[:1]
        if heading:
            text = "\n".join(lines[len(heading):]).strip()
        para = parsed.get("paragraph")
        if para:
            parts = lsource.split_paragraphs(text)
            hit = next((p["text"] for p in parts if p["number"] == para), None)
            if hit is None:
                if not parts and para == "1":
                    result["paragraph_note"] = "This provision has no numbered paragraphs; the whole text is returned."
                    hit = text
                else:
                    raise LawError("paragraph_not_found", f"параграф ({para}) does not exist in this article.",
                                   "Read it without paragraph, or pick one of the listed параграфи.",
                                   {"paragraphs": [p["number"] for p in parts]})
            text = hit
            result["paragraph"] = para
        cut = _cut(text, max_chars)
        facts = cached.data.get("facts", {})
        short = act.get(lang) or act.get("bg") or celex
        labels = {"article": ("чл.", "Art."), "recital": ("съображение", "Recital"),
                  "annex": ("приложение", "Annex")}
        word = labels[kind][0] if lang == "bg" else labels[kind][1]
        cite = f"{word} {number}"
        if result.get("paragraph"):
            cite += f", пар. {result['paragraph']}" if lang == "bg" else f"({result['paragraph']})"
        for key in ("point", "letter"):
            if parsed.get(key):
                if lang == "bg":
                    cite += f", т. {parsed[key]}" if key == "point" else f", б. „{parsed[key]}“"
                else:
                    cite += f"({parsed[key]})"
        provision: Dict[str, Any] = {"citation": f"{cite} от {short}" if lang == "bg" else f"{cite} {short}",
                                     "id": uid, "title": heading[1] if len(heading) > 1 else "", **cut}
        if cut["truncated"]:
            provision["next_step"] = f"Text cut at {max_chars} characters; call again with max_chars up to 30000."
        version: Dict[str, Any] = {"document": doc_id, "consolidated_as_of": consolidated, "language": lang}
        for key in ("official_journal", "amended_by", "corrected_by", "legal_effect"):
            if facts.get(key):
                version[key] = facts[key]
        if note:
            version["note"] = note
        result.update({
            "law": {"abbreviation": short, "title": facts.get("title", ""), "celex": celex},
            "provision": provision, "version": version,
            "source": self._source_eu(doc_id, consolidated, lang, cached),
        })
        stale = self._stale_note(cached)
        if stale:
            result["stale"] = True
            result["stale_note"] = stale
        return result

    def _eu_doc(self, act: Dict[str, Any], lang: str, original: bool = False):
        """(cached document, document id, consolidated date, note). Recitals come from the original
        Official Journal text because consolidated texts omit them."""
        celex = act["celex"]
        note = None
        if original:
            doc_id, consolidated = celex, None
        else:
            doc_id, consolidated, note = self.eu.pick_version(celex)
        try:
            cached = self.eu.document(doc_id, lang)
        except LawError as err:
            if err.kind != "not_found" or doc_id == celex:
                if err.kind == "not_found":
                    raise self._not_covered(act, lang) from err
                raise
            note = f"The consolidated text {doc_id} is not available as structured text; original text used."
            doc_id, consolidated = celex, None
            try:
                cached = self.eu.document(doc_id, lang)
            except LawError as err2:
                if err2.kind == "not_found":
                    raise self._not_covered(act, lang) from err2
                raise
        if not cached.data.get("units"):
            raise self._not_covered(act, lang)
        if original:
            note = "Recitals are read from the original Official Journal text (consolidated texts omit them)."
        return cached, doc_id, consolidated, note

    @staticmethod
    def _not_covered(act: Dict[str, Any], lang: str) -> LawError:
        return LawError("not_covered",
                        f"CELEX {act['celex']} is not available as structured (ELI) text from the Publications "
                        "Office. Older acts and the EU treaties are not covered.",
                        f"Do not quote it from memory; give the user the EUR-Lex page "
                        f"{eu_mod.human_url(act['celex'], lang)}.")

    # ---------------------------------------------------------- bg_law_search
    def law_search(self, args: Dict[str, Any]) -> Dict[str, Any]:
        query = str(args.get("query") or args.get("law") or "").strip()
        if not query:
            raise LawError("bad_argument", "No search words given.", "Pass an act name, abbreviation or title word.")
        limit = _int(args.get("limit"), 10, 1, 50)
        kind = str(args.get("kind") or "").strip()
        results: List[Dict[str, Any]] = []
        if self._flag("eu_law"):
            for act in eu_mod.search_known(query):
                results.append({"jurisdiction": "EU", "law": act["bg"],
                                "abbreviation": f"{act['bg']} / {act['en']}" if act["bg"] != act["en"] else act["bg"],
                                "celex": act["celex"], "match": "EU act"})
        note = None
        try:
            rows = self.bg.search(query, limit, kind)
        except LawError as err:
            if err.kind == "key_required":
                rows = []
                note = ("Bulgarian statutes need the free lsource.net API key (settings, or LSOURCE_API_KEY); "
                        "only EU acts are searched without it.")
            else:
                raise
        for row in rows:
            results.append({"jurisdiction": "BG", "law": row.get("id", ""), "title": row.get("title", ""),
                            "abbreviation": ABBR_BY_ID.get(row.get("id", ""), ""),
                            "kind": row.get("kind", ""), "issue": row.get("issue", ""),
                            "changed": row.get("changed", ""), "url": lsource.public_url(row.get("id", "")),
                            "match": "abbreviation" if ABBR_BY_ID.get(row.get("id", ""), "").lower() == query.lower()
                                     else "title"})
        payload: Dict[str, Any] = {"ok": True, "query": query, "total_matches": len(results),
                                   "results": results[:limit],
                                   "source": {"name": "Питай законите (lsource.net) and the plugin's EU act list",
                                              "url": f"{lsource.API}/acts?q=", "license": "CC BY 4.0"}}
        if note:
            payload["note"] = note
        if not results:
            payload["message"] = "No Bulgarian act or known EU act matches."
            payload["next_step"] = ("Try a shorter title word or the official abbreviation. Municipal наредби and "
                                    "EU treaties are not covered; an EU act can be named by its CELEX number.")
        else:
            payload["next_step"] = "Read a provision with bg_law_get (law = the 'law' value, article = the number)."
        return payload

    # ------------------------------------------------------------- bg_law_toc
    def law_toc(self, args: Dict[str, Any]) -> Dict[str, Any]:
        law = str(args.get("law") or "").strip()
        if not law:
            raise LawError("bad_argument", "No act given.", "Pass an act, e.g. 'КТ' or 'ОРЗД'.")
        query = str(args.get("query") or "").strip()
        offset = _int(args.get("offset"), 0, 0, 100000)
        limit = _int(args.get("limit"), 50, 1, 200)
        act = eu_mod.match_eu(law)
        if act:
            if not self._flag("eu_law"):
                raise LawError("disabled", "EU law lookups are switched off in this plugin's settings (eu_law).")
            entries, head, source = self._eu_toc(act, self._eu_lang(args.get("language")))
        else:
            entries, head, source = self._bg_toc(law)
        if query:
            q = lsource.fold(query)
            hits = []
            for entry in entries:
                text = entry.pop("_text", "")
                where = lsource.fold(entry.get("title", "")).find(q)
                if where >= 0 or lsource.fold(entry.get("scope", "")).find(q) >= 0:
                    entry["match"] = "heading"
                    hits.append(entry)
                    continue
                pos = lsource.fold(text).find(q)
                if pos >= 0:
                    start = max(0, pos - 80)
                    entry["match"] = "text"
                    entry["snippet"] = ("…" if start else "") + " ".join(text[start:pos + len(query) + 120].split()) + "…"
                    hits.append(entry)
            entries = hits
        else:
            for entry in entries:
                entry.pop("_text", None)
        page = entries[offset:offset + limit]
        payload = {"ok": True, **head, "query": query or None, "total": len(entries), "offset": offset,
                   "entries": page, "source": source}
        if offset + limit < len(entries):
            payload["next_step"] = f"More entries: call again with offset={offset + limit}."
        if query and not entries:
            payload["message"] = f"No heading or text in this act contains '{query}'."
        return payload

    def _bg_toc(self, law: str):
        act_id, title = self.bg.resolve(law)
        try:
            meta = self.bg.meta(act_id)
        except LawError as err:
            if err.kind == "not_found":
                raise LawError("law_not_found", f"No Bulgarian act with id '{act_id}'.") from err
            raise
        cached = self.bg.act(act_id)
        data = cached.data
        entries = []
        for art in data["articles"]:
            entries.append({"id": art["ref"], "title": art["heading"], "scope": art["scope"],
                            "repealed": art.get("repealed", False), "_text": art["text"]})
        act_title = data["meta"].get("title") or title
        head = {"jurisdiction": "BG",
                "law": {"abbreviation": ABBR_BY_ID.get(act_id, ""), "title": act_title, "id": act_id,
                        "kind": data["meta"].get("kind", ""), "url": lsource.public_url(act_id)},
                "version": {"status": meta.data.get("status", ""),
                            "issue": meta.data.get("issue", "") or (
                                (data["meta"].get("gazette_chain") or [{}])[-1].get("issue", "")),
                            "changed": meta.data.get("changed", "") or (
                                (data["meta"].get("gazette_chain") or [{}])[-1].get("date", "")),
                            "official": False,
                            "chapters": [c["heading"] + (" " + c["title"] if c["title"] else "")
                                         for c in data["chapters"]]}}
        return entries, head, self._source_bg(act_id, act_title, meta.data, cached)

    def _eu_toc(self, act: Dict[str, Any], lang: str):
        cached, doc_id, consolidated, note = self._eu_doc(act, lang)
        entries = []
        for uid, unit in cached.data["units"].items():
            lines = unit["text"].split("\n")
            kind = uid.split("_")[0]
            title = lines[1] if len(lines) > 1 and not _PARA.match(lines[1]) else ""
            if lang == "bg":
                label = {"art": "чл.", "anx": "приложение", "rct": "съображение"}[kind]
            else:
                label = {"art": "Art.", "anx": "Annex", "rct": "Recital"}[kind]
            number = uid.split("_", 1)[1]
            entries.append({"id": f"{label} {number}", "title": title if kind != "rct" else "",
                            "_text": unit["text"]})
        facts = cached.data.get("facts", {})
        head = {"jurisdiction": "EU", "law": {"abbreviation": act.get(lang) or act["celex"], "celex": act["celex"],
                                             "title": facts.get("title", "")},
                "version": {"document": doc_id, "consolidated_as_of": consolidated, "note": note}}
        return entries, head, self._source_eu(doc_id, consolidated, lang, cached)

    # --------------------------------------------------------- bg_law_history
    def law_history(self, args: Dict[str, Any]) -> Dict[str, Any]:
        law = str(args.get("law") or args.get("citation") or "").strip()
        if not law:
            raise LawError("bad_argument", "No act given.", "Pass an act, e.g. 'КТ'.")
        limit = _int(args.get("limit"), 40, 1, 300)
        act_id, title = self.bg.resolve(law)
        cached = self.bg.act(act_id)
        data = cached.data
        meta = self.bg.meta(act_id)
        chain = data["meta"].get("gazette_chain") or []
        try:
            hist = self.bg.history(act_id).data
        except LawError as err:
            if err.kind == "not_found":
                hist = {}
            else:
                raise
        coverage = hist.get("coverage", {}) if isinstance(hist, dict) else {}
        versions = hist.get("versions", []) if isinstance(hist, dict) else []
        payload: Dict[str, Any] = {
            "ok": True, "jurisdiction": "BG",
            "law": {"id": act_id, "title": data["meta"].get("title") or title,
                    "abbreviation": ABBR_BY_ID.get(act_id, ""), "kind": data["meta"].get("kind", ""),
                    "url": lsource.public_url(act_id)},
            "status": {"state": meta.data.get("status", ""),
                       "changed": meta.data.get("changed") or (chain[-1]["date"] if chain else ""),
                       "issue": meta.data.get("issue") or (chain[-1]["issue"] if chain else ""),
                       "action": meta.data.get("action") or (chain[-1]["action"] if chain else ""),
                       "replaced_by": meta.data.get("replaced_by", ""),
                       "in_force_from": data["meta"].get("in_force_from", ""),
                       "official": False, "audit_score": meta.data.get("audit_score")},
            "gazette_chain": {"total": len(chain), "entries": chain[-limit:] if limit else chain,
                              "note": ("The full promulgation chain of the act, oldest first, parsed from the "
                                       "consolidated text itself. The last entry is the one that produced the "
                                       "current text.")},
            "point_in_time": {"coverage": coverage, "versions": versions,
                              "note": ("Where the corpus has a reconstructed history, bg_law_get accepts "
                                       "as_of=YYYY-MM-DD and bg_law_history lists the DВ issues it can serve. "
                                       "An empty versions list means no point-in-time text exists for this act.")},
            "source": self._source_bg(act_id, data["meta"].get("title") or title, meta.data, cached),
        }
        if not coverage.get("has_history"):
            payload["point_in_time"]["limitation"] = ("No reconstructed history for this act: only the current "
                                                      "consolidated text is available.")
        elif coverage.get("earliest"):
            payload["point_in_time"]["limitation"] = (f"Point-in-time text exists from {coverage.get('earliest')} "
                                                      f"({coverage.get('count')} versions). Older dates fall back to "
                                                      f"the current text, and the answer must say so.")
        stale = self._stale_note(cached)
        if stale:
            payload["stale"] = True
            payload["stale_note"] = stale
        return payload

    # --------------------------------------------------------- bg_case_search
    def case_search(self, args: Dict[str, Any]) -> Dict[str, Any]:
        if not self._flag("case_law"):
            raise LawError("disabled", "Case-law lookups are switched off in this plugin's settings (case_law).")
        dfrom = str(args.get("date_from") or "").strip()
        dto = str(args.get("date_to") or "").strip()
        for value in (dfrom, dto):
            if value and not re.fullmatch(r"\d{4}(-\d{2}(-\d{2})?)?", value):
                raise LawError("bad_argument", f"'{value}' is not a date.", "Use YYYY-MM-DD, YYYY-MM or YYYY.")
        case_number = str(args.get("case_number") or "").strip()
        query = str(args.get("query") or "").strip()
        act_type = str(args.get("act_type") or "").strip()
        if not (case_number or query or act_type or dfrom or dto):
            raise LawError("bad_argument", "Give at least a case number, an act type, a date range or keywords.",
                           "Example: case_number='15/2026', or act_type='решение' with date_from='2026-01-01'.")
        if dto and len(dto) < 10:
            dto = dto + "-99" if len(dto) == 7 else dto + "-12-31"
        limit = _int(args.get("limit"), 10, 1, 50)
        scan_limit = _int(args.get("scan_limit"), 8, 1, 25)
        rows, scanned, capped = self.cases.search(case_number or None, dfrom or None, dto or None,
                                                 act_type or None, query or None, scan_limit)
        index = self.cases.index()
        payload: Dict[str, Any] = {
            "ok": True, "total_matches": len(rows),
            "results": rows[:limit],
            "covers": ("Published acts of the Bulgarian Constitutional Court only: решения, определения, "
                       "становища and особени мнения. The Supreme Courts (ВКС, ВАС), appellate and first-instance "
                       "courts are not covered."),
            "source": {"name": "Конституционен съд на Република България (search-acts index)",
                       "url": constcourt.LIST_URL, "retrieved_at": _iso_ts(index.fetched_at),
                       "cache": index.state, "acts_indexed": len(index.data)},
        }
        if query:
            payload["keyword_scan"] = {"acts_downloaded": scanned, "capped": capped,
                                       "note": ("Keywords are matched inside the acts themselves, so a keyword "
                                                "search downloads the newest candidates up to scan_limit. Narrow "
                                                "with a date or act type, or raise scan_limit.")}
        if not rows:
            payload["message"] = "No published Constitutional Court act matches."
            payload["next_step"] = ("Widen or narrow the date range, drop the keywords and read by case number, "
                                    "or note that other courts are not covered.")
        else:
            payload["next_step"] = "Read one with bg_case_get(doc_id=...)."
        return payload

    # ------------------------------------------------------------ bg_case_get
    def case_get(self, args: Dict[str, Any]) -> Dict[str, Any]:
        if not self._flag("case_law"):
            raise LawError("disabled", "Case-law lookups are switched off in this plugin's settings (case_law).")
        max_chars = _int(args.get("max_chars"), 8000, 500, 60000)
        celex = str(args.get("celex") or "").strip()
        doc_id = str(args.get("doc_id") or "").strip()
        case_number = str(args.get("case_number") or "").strip()
        if celex:
            return self._judgment(celex, self._eu_lang(args.get("language")), args.get("paragraph"), max_chars)
        if not doc_id and case_number:
            rows, _scanned, _capped = self.cases.search(case_number, None, None, None, None, 0)
            if len(rows) == 1:
                doc_id = rows[0]["doc_id"]
            elif not rows:
                raise LawError("case_not_found", f"No Constitutional Court act for case {case_number}.",
                               "Check the case number, or search by act type and date range.")
            else:
                raise LawError("ambiguous_case", f"{len(rows)} acts exist for case {case_number}.",
                               "Pick one doc_id from candidates.",
                               {"candidates": [{k: r[k] for k in ("doc_id", "act_type", "date", "case_number")}
                                               for r in rows[:10]]})
        if not doc_id:
            raise LawError("bad_argument", "Pass doc_id, case_number or celex.")
        act = self.cases.act(doc_id)
        cut = _cut(act["text"], max_chars)
        payload: Dict[str, Any] = {
            "ok": True, "jurisdiction": "BG",
            "decision": {"doc_id": f"act-{act['doc_id']}" if act["doc_id"] else doc_id,
                         "act_type": act["act_type"], "date": act["date"],
                         "case_number": act["case_number"], "url": act["url"]},
            "parts": {"text": cut["text"]}, "truncated": cut["truncated"],
        }
        if cut["truncated"]:
            payload["next_step"] = f"Text cut at {max_chars} characters; call again with max_chars up to 60000."
        payload["source"] = {"name": "Конституционен съд на Република България", "url": act["url"],
                             "license": "public", "attribution": self._case_attribution(act)}
        return payload

    def _case_attribution(self, act: Dict[str, Any]) -> str:
        lang = self._lang("bg")
        case = act.get("case_number", "")
        if lang == "bg":
            return (f"Източник: Конституционен съд на Република България (www.constcourt.bg), "
                    f"{act.get('act_type', 'акт')} от {act.get('date', '')}"
                    + (f" по к.д. № {case}" if case else "")
                    + "; извадка, създадена с hermes-bg-law.")
        return (f"Source: Constitutional Court of the Republic of Bulgaria (www.constcourt.bg), "
                f"{act.get('act_type', 'act')} of {act.get('date', '')}"
                + (f" in case No. {case}" if case else "")
                + "; excerpt made with hermes-bg-law.")

    def _judgment(self, name: str, lang: str, paragraph: Any, max_chars: int) -> Dict[str, Any]:
        case = eu_mod.match_case(name)
        if not case:
            raise LawError("bad_argument", f"'{name}' is not a CELEX number of a Court of Justice judgment.",
                           "Pass a judgment CELEX like 62018CJ0311, a case number like C-311/18, or a known "
                           "name like 'Schrems II'.")
        celex = case["celex"]
        cached = self.eu.judgment(celex, lang)
        doc = cached.data
        points = doc.get("points", {})
        para = str(paragraph or "").strip()
        if para:
            if not para.isdigit() or para not in points:
                raise LawError("paragraph_not_found", f"This judgment has no point {para}.",
                               "Read it without paragraph, or pass one of the numbered points it has.",
                               {"points": len(points)})
            text = points[para]
        else:
            text = doc.get("text", "")
        cut = _cut(text, max_chars)
        title = case.get("name") or celex
        if lang == "bg":
            attribution = (f"Източник: Съд на Европейския съюз, {title} (CELEX {celex}), през Службата за "
                           f"публикации на ЕС (publications.europa.eu), © Европейски съюз, CC BY 4.0; изтеглено "
                           f"на {_day(lang, cached.fetched_at)}; извадка, създадена с hermes-bg-law.")
        else:
            attribution = (f"Source: Court of Justice of the European Union, {title} (CELEX {celex}), via the "
                           f"Publications Office of the EU (publications.europa.eu), © European Union, CC BY 4.0; "
                           f"retrieved {_day(lang, cached.fetched_at)}; excerpt made with hermes-bg-law.")
        payload: Dict[str, Any] = {
            "ok": True, "jurisdiction": "EU",
            "decision": {"celex": celex, "name": title, "paragraphs": len(points),
                         "url": eu_mod.human_url(celex, lang)},
            "parts": {"text": cut["text"]}, "truncated": cut["truncated"],
            "source": {"name": "Publications Office of the EU (CELLAR)", "url": eu_mod.human_url(celex, lang),
                       "data_url": eu_mod.CELLAR + celex, "license": "CC BY 4.0",
                       "retrieved_at": _iso_ts(cached.fetched_at), "cache": cached.state,
                       "attribution": attribution},
        }
        if para:
            payload["paragraph"] = para
        if cut["truncated"]:
            payload["next_step"] = (f"Text cut at {max_chars} characters; call again with max_chars up to 60000, "
                                    "or ask for one paragraph by number.")
        stale = self._stale_note(cached)
        if stale:
            payload["stale"] = True
            payload["stale_note"] = stale
        return payload


def _as_handler(fn: Callable[[Dict[str, Any]], Dict[str, Any]], name: str) -> Callable:
    def handler(args: Optional[dict] = None, **_kwargs) -> str:
        try:
            if not isinstance(args, dict):
                args = {}
            return _dump(fn(dict(args)))
        except LawError as err:
            return _error(err.kind, err.message, err.hint, err.details)
        except Exception as err:  # never let a tool call break the agent loop
            logger.exception("bg-law tool %s failed", name)
            return _error("internal", f"Unexpected error in the bg-law plugin: {type(err).__name__}: {err}",
                          "Retry once. If it repeats, tell the user the lookup failed and link the source site.")
    handler.__name__ = name
    return handler


def make_handlers(tools: Optional[Tools] = None) -> Dict[str, Callable]:
    t = tools or Tools()
    return {
        "bg_law_get": _as_handler(t.law_get, "bg_law_get"),
        "bg_law_search": _as_handler(t.law_search, "bg_law_search"),
        "bg_law_toc": _as_handler(t.law_toc, "bg_law_toc"),
        "bg_law_history": _as_handler(t.law_history, "bg_law_history"),
        "bg_case_search": _as_handler(t.case_search, "bg_case_search"),
        "bg_case_get": _as_handler(t.case_get, "bg_case_get"),
    }
