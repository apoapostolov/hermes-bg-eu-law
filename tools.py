"""Tool handlers. Every handler returns a JSON string and never raises: bad arguments,
source outages and bugs come back as {"ok": false, "error", "error_type", "next_step"}."""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import date
from typing import Any, Callable, Dict, List, Optional

from . import constcourt, eu as eu_mod, lsource
from .citations import cyrillic_letter, parse_citation, split_section
from .client import LawError, Store

logger = logging.getLogger(__name__)

DEFAULT_MAX_CHARS = 6000
DISCLAIMER = ("Legal texts, not legal advice. The version promulgated in Държавен вестник, and the "
              "Official Journal for EU acts, is the binding one.")
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
    payload["next_step"] = hint or "Check the identifier, source settings and available history, then retry."
    return _dump(payload)


def _int(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        num = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return max(lo, min(hi, num))


def _day(lang: str, ts: float) -> str:
    """Date the text was fetched from (or last confirmed by) the source."""
    return time.strftime("%d.%m.%Y", time.gmtime(ts or time.time()))


def _cut(text: str, max_chars: int, offset: int = 0) -> Dict[str, Any]:
    end = min(len(text), offset + max_chars)
    result = {"text": text[offset:end], "truncated": end < len(text),
              "total_chars": len(text), "offset": offset}
    if end < len(text):
        result.update(next_offset=end, next_step=f"Continue with offset={end} and the same provision and revision.")
    return result


def _date(value):
    value = str(value or "").strip()
    if value:
        try:
            if date.fromisoformat(value).isoformat() != value:
                raise ValueError()
        except ValueError:
            raise LawError("bad_argument", f"'{value}' is not a calendar date.", "Use YYYY-MM-DD.")
    return value


def _freshness(payload, *copies):
    payload["freshness"] = [{"url": c.meta.get("url", ""), "cache": c.state,
                             "retrieved_at": _iso_ts(c.fetched_at)} for c in copies]
    stale = [c for c in copies if c.state == "stale"]
    if stale:
        payload["stale"] = True
        payload["stale_note"] = "One or more sources could not be rechecked. Quote the cached dates in freshness."


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
        from .settings import runtime_store
        self.store = store or runtime_store()
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
        revision = str(args.get("revision") or "").strip()
        offset = _int(args.get("offset"), 0, 0, 100000000)
        if act:
            if not self._flag("eu_law"):
                raise LawError("disabled", "EU law lookups are switched off in this plugin's settings (eu_law).")
            if args.get("as_of"):
                raise LawError("bad_argument", "EU acts need an explicit revision from bg_law_history, not as_of.")
            if revision:
                if not re.fullmatch(r"0" + re.escape(act["celex"][1:]) + r"-\d{8}", revision):
                    raise LawError("bad_argument", "The revision must identify a consolidation of this act.")
                _date(f"{revision[-8:-4]}-{revision[-4:-2]}-{revision[-2:]}")
                act["document"] = revision
            return self._eu_get(act, parsed, self._eu_lang(args.get("language")), max_chars, offset)
        if not self._flag("bg_law"):
            raise LawError("disabled", "Bulgarian legislation is switched off in settings.")
        if revision and not re.fullmatch(r"[a-fA-F0-9]{6,64}", revision):
            raise LawError("bad_argument", "Use a revision hash from bg_law_history.")
        if revision and args.get("as_of"):
            raise LawError("bad_argument", "Pass revision or as_of, not both.")
        return self._bg_get(law, parsed, max_chars, args.get("as_of"), revision, offset)

    def _versions_until(self, act_id: str, as_of: str) -> tuple:
        """(reconstructed version dates at or before as_of, newest first, coverage dict)."""
        try:
            hist = self.bg.history(act_id).data
        except LawError as err:
            if err.kind != "not_found":
                raise
            return [], {}
        coverage = hist.get("coverage", {}) if isinstance(hist, dict) else {}
        dates = sorted({str(v.get("date", "")) for v in hist.get("versions", []) if isinstance(v, dict)},
                       reverse=True)
        return [d for d in dates if d and d <= as_of], coverage

    def _bg_get(self, law, parsed, max_chars, as_of=None, revision="", offset=0):
        from .provisions import akn_pinpoint, pinpoint
        as_of = _date(as_of)
        act_id, title = self.bg.resolve(law)
        meta = self.bg.meta(act_id)
        number = parsed.get("number") or ""
        kind = parsed.get("kind") or "article"
        historical = bool(as_of or revision)
        result = {"ok": True, "jurisdiction": "BG"}
        snapshot = {}
        if revision or (as_of and kind == "paragraph_section"):
            try:
                cached = self.bg.act(act_id, as_of=as_of, revision=revision)
            except LawError as err:
                if err.kind != "not_found":
                    raise
                raise LawError("as_of_not_available", "The requested historical document is unavailable.",
                               "Read history and select a revision the source can serve.",
                               {"coverage": meta.data.get("history", {})}) from err
            data = cached.data
            art = lsource.find_article(data, number, kind)
            snapshot = data["meta"]
        elif as_of:
            try:
                cached = self.bg.article_as_of(act_id, number, as_of)
            except LawError as err:
                if err.kind != "not_found":
                    raise
                candidates, coverage = self._versions_until(act_id, as_of)
                for candidate in candidates[:30]:
                    try:
                        cached = self.bg.article_as_of(act_id, number, candidate)
                        break
                    except LawError as inner:
                        if inner.kind != "not_found":
                            raise
                else:
                    raise LawError("as_of_not_available", f"No reconstructed text for {act_id} on {as_of}.",
                                   "Read the available history; do not substitute today's text.",
                                   {"coverage": coverage}) from err
            snapshot = cached.data
            if snapshot.get("id") and snapshot["id"] not in (act_id, meta.data.get("id")):
                raise LawError("bad_response", "The historical response identifies a different act.")
            if not str(snapshot.get("text") or "").strip():
                raise LawError("bad_response", "The historical article body is empty.")
            if str(snapshot.get("number", number)) != number:
                raise LawError("bad_response", "The historical response identifies a different article.")
            if snapshot.get("ref") and not snapshot["ref"].lower().startswith("чл"):
                raise LawError("bad_response", "The historical response identifies a different provision family.")
            art = {"number": number, "ref": f"Чл. {number}",
                   "heading": "", "scope": snapshot.get("scope", ""), "text": snapshot["text"]}
            art["repealed"] = bool(re.match(r"^Чл[.]?\s*\w+[.]?\s*\(?Отм[.]", art["text"], re.I))
            data = {"meta": {"title": snapshot.get("law") or title, "gazette_chain": []}}
        else:
            cached = self.bg.act(act_id)
            data = cached.data
            art = lsource.find_article(data, number, kind)
        if art is None:
            raise LawError("article_not_found", f"No {kind} {number} exists in this revision.",
                           "Do not guess a nearby number. Call bg_law_toc for the available provisions.")
        if historical:
            used = snapshot.get("at_date") or ""
            if not used:
                raise LawError("bad_response", "The source did not identify the historical revision date.",
                               "Read history and request a dated revision before quoting.")
            if as_of and used > as_of:
                raise LawError("as_of_not_available", "The source returned a revision newer than the requested date.")
            result.update(as_of=as_of or used, as_of_used=used,
                          as_of_note="Revision promulgated by the requested date; this is not an effective-date determination.")
        text = art["text"]
        para = parsed.get("paragraph")
        if para:
            parts = lsource.split_paragraphs(text)
            hit = next((p["text"] for p in parts if p["number"] == para), None)
            if hit is None:
                if not parts and para == "1":
                    result["paragraph_note"] = "This provision has no numbered paragraphs; the whole text is returned."
                    hit = text
                else:
                    raise LawError("paragraph_not_found", f"No paragraph {para} exists in this revision.",
                                   "Use a paragraph actually listed in the provision.",
                                   {"paragraphs": [p["number"] for p in parts]})
            text = hit
            result["paragraph"] = para
        pins = {k: parsed[k] for k in ("point", "letter") if parsed.get(k)}
        pin_cached = None
        if pins:
            normalized = dict(parsed)
            if normalized.get("letter"):
                normalized["letter"] = cyrillic_letter(normalized["letter"])
            try:
                if kind != "article":
                    raise LawError("pinpoint_unavailable", "Transitional provision uses text markers.")
                pin_cached = self.bg.akn(act_id, result.get("as_of_used", ""))
                text = akn_pinpoint(pin_cached.data, number, normalized)
            except LawError as err:
                if err.kind not in ("not_found", "pinpoint_unavailable"):
                    raise
                text = pinpoint(text, normalized)
            result.update(pinpoint=pins, pinpoint_verified=True)
        chain = data["meta"].get("gazette_chain") or []
        latest = chain[-1] if chain else None
        act_title = data["meta"].get("title") or title
        issue = snapshot.get("issue", "") if historical else meta.data.get("issue") or (latest or {}).get("issue", "")
        changed = result.get("as_of_used", "") if historical else meta.data.get("changed") or (latest or {}).get("date", "")
        result.update(
            law={"id": act_id, "title": act_title, "abbreviation": ABBR_BY_ID.get(act_id, ""),
                 "kind": meta.data.get("kind", ""), "url": lsource.public_url(act_id)},
            provision={"citation": _cite_bg(art, parsed, act_title, ABBR_BY_ID.get(act_id, "")),
                       "id": art["ref"], "heading": art["heading"], "scope": art["scope"],
                       **_cut(text, max_chars, offset)},
            version={"status": meta.data.get("status", ""), "status_as_of": "current",
                     "issue": issue, "changed": changed, "official": False,
                     "audit_score": meta.data.get("audit_score"), "gazette_latest": latest,
                     "gazette_count": len(chain), "in_force_from": data["meta"].get("in_force_from", ""),
                     "note": "Status describes the current whole act; issue and changed identify the quoted revision."},
            source=self._source_bg(act_id, act_title, {"issue": issue, "changed": changed}, cached))
        if art.get("repealed"):
            result["provision"]["repealed"] = True
        _freshness(result, cached, meta, *([pin_cached] if pin_cached else []))
        return result

    def _eu_get(self, act: Dict[str, Any], parsed: Dict[str, Any], lang: str, max_chars: int, offset=0) -> Dict[str, Any]:
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
        from .provisions import eu_letter, pinpoint
        parsed = dict(parsed)
        if parsed.get("letter"):
            parsed["letter"] = eu_letter(parsed["letter"], lang)
        text = pinpoint(text, parsed)
        cut = _cut(text, max_chars, offset)
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
            provision["next_step"] = cut["next_step"]
        version: Dict[str, Any] = {"document": doc_id, "celex": celex, "consolidated_as_of": consolidated,
                                   "language": lang}
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
        index = getattr(self.eu._local, "versions", None) if kind != "recital" else None
        _freshness(result, cached, *([index] if index else []))
        if note and "could not be listed" in note:
            result.update(stale=True, stale_note=note)
        return result

    def _eu_doc(self, act: Dict[str, Any], lang: str, original: bool = False):
        """(cached document, document id, consolidated date, note). Recitals come from the original
        Official Journal text because consolidated texts omit them."""
        celex = act["celex"]
        note = None
        if original and act.get("document"):
            raise LawError("as_of_not_available", "Consolidated snapshots do not contain recitals.",
                           "Read the original recital without revision; no original text was silently substituted.")
        if original:
            doc_id, consolidated = celex, None
        elif act.get("document"):
            doc_id = act["document"]
            consolidated = f"{doc_id[-8:-4]}-{doc_id[-4:-2]}-{doc_id[-2:]}"
        else:
            doc_id, consolidated, note = self.eu.pick_version(celex)
        try:
            cached = self.eu.document(doc_id, lang)
        except LawError as err:
            if act.get("document"):
                raise LawError("as_of_not_available", "The requested EU snapshot could not be read.",
                               "Choose a document listed by bg_law_history; no newer text was substituted.") from err
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
    def law_search(self, args):
        query = str(args.get("query") or args.get("law") or "").strip()
        if not query:
            raise LawError("bad_argument", "No search words given.", "Pass an act title or abbreviation.")
        limit, offset = _int(args.get("limit"), 10, 1, 50), _int(args.get("offset"), 0, 0, 1000000)
        kind = str(args.get("kind") or "").strip()
        jurisdiction = args.get("jurisdiction", "auto")
        if jurisdiction not in ("auto", "BG", "EU"):
            raise LawError("bad_argument", "jurisdiction must be auto, BG or EU.")
        act = eu_mod.match_eu(query)
        if jurisdiction == "EU" or (jurisdiction == "auto" and act):
            if not self._flag("eu_law"):
                raise LawError("disabled", "EU legislation is switched off in settings.")
            if act:
                actual_kind = {"R": "регламент", "L": "директива", "D": "решение"}.get(act["celex"][5], "")
                rows = [{"jurisdiction": "EU", "law": act["celex"], "celex": act["celex"],
                         "title": act.get("bg", ""), "kind": actual_kind, "abbreviation": act.get("en", "")}]
                if kind and kind != actual_kind:
                    rows = []
                return {"ok": True, "results": rows[offset:offset + limit], "total_matches": len(rows),
                        "offset": offset, "source": {"url": eu_mod.human_url(act["celex"], "bg"), "license": "CC BY 4.0"}}
            cached = self.eu.search(query, limit, offset, kind=kind)
            result = {"ok": True, **cached.data, "source": {"url": eu_mod.SPARQL, "cache": cached.state,
                      "license": "CC BY 4.0", "retrieved_at": _iso_ts(cached.fetched_at)}}
            _freshness(result, cached)
            return result
        if not self._flag("bg_law"):
            raise LawError("disabled", "Bulgarian legislation is switched off in settings.")
        alias_id = lsource.ALIASES.get(lsource.fold(query)) or lsource._loose_alias(query)
        if alias_id:
            cached = self.bg.meta(alias_id)
            rows = [cached.data] if not kind or kind == cached.data.get("kind") else []
            total = len(rows)
            rows = rows[offset:offset + limit]
        else:
            try:
                cached = self.bg.search_page(query, limit, kind, offset)
            except LawError as err:
                if err.kind != "key_required":
                    raise
                return {"ok": True, "results": [], "total_matches": 0,
                        "note": "Bulgarian statutes require the free lsource key. Use jurisdiction=EU for EU title search.",
                        "source": {"url": lsource.DOCS_PAGE, "license": "CC BY 4.0"}}
            rows, total = cached.data.get("results", []), cached.data.get("total", 0)
        results = [{**r, "jurisdiction": "BG", "law": r.get("id", ""),
                    "match": "abbreviation" if alias_id else "title",
                    "abbreviation": ABBR_BY_ID.get(r.get("id", ""), ""),
                    "url": lsource.public_url(r.get("id", ""))} for r in rows]
        result = {"ok": True, "query": query, "total_matches": total, "results": results,
                  "offset": offset, "next_offset": offset + len(rows) if rows and offset + len(rows) < total else None,
                  "source": {"url": lsource.API + "/acts", "license": "CC BY 4.0",
                             "cache": cached.state, "retrieved_at": _iso_ts(cached.fetched_at)}}
        _freshness(result, cached)
        return result

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
        if not self._flag("bg_law"):
            raise LawError("disabled", "Bulgarian legislation is switched off in settings.")
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
        _freshness(head, cached, meta)
        return entries, head, self._source_bg(act_id, act_title, meta.data, cached)

    @staticmethod
    def _eu_entries(doc: Dict[str, Any], lang: str) -> List[Dict[str, Any]]:
        entries: List[Dict[str, Any]] = []
        for uid, unit in doc["units"].items():
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
        return entries

    def _eu_toc(self, act: Dict[str, Any], lang: str):
        cached, doc_id, consolidated, note = self._eu_doc(act, lang)
        index = getattr(self.eu._local, "versions", None)
        alt = None
        entries = self._eu_entries(cached.data, lang)
        prefix = "съображение" if lang == "bg" else "Recital"
        if not any(entry["id"].startswith(prefix) for entry in entries):
            # Consolidated texts drop the recitals, and a recital is citable, so it has to be listed.
            try:
                alt, _alt_id, _alt_cons, _alt_note = self._eu_doc(act, lang, original=True)
            except LawError:
                alt = None
            if alt is not None:
                recitals = [e for e in self._eu_entries(alt.data, lang) if e["id"].startswith(prefix)]
                if recitals:
                    entries = recitals + entries
                    extra = ("Recitals are listed from the original Official Journal text; the consolidated "
                             "text omits them.")
                    note = f"{note} {extra}" if note else extra
        facts = cached.data.get("facts", {})
        head = {"jurisdiction": "EU", "law": {"abbreviation": act.get(lang) or act["celex"], "celex": act["celex"],
                                             "title": facts.get("title", "")},
                "version": {"document": doc_id, "celex": act["celex"], "consolidated_as_of": consolidated,
                            "note": note}}
        _freshness(head, cached, *([index] if index else []), *([alt] if alt else []))
        if note and "could not be listed" in note:
            head.update(stale=True, stale_note=note)
        return entries, head, self._source_eu(doc_id, consolidated, lang, cached)

    def _eu_history(self, act: Dict[str, Any], lang: str, limit: int) -> Dict[str, Any]:
        """Consolidated versions published for one CELEX, newest first."""
        celex = act["celex"]
        cached, doc_id, consolidated, note = self._eu_doc(act, lang)
        facts = cached.data.get("facts", {})
        versions_unavailable = False
        try:
            versions = self.eu.versions(celex)
        except LawError as err:
            if err.kind not in ("not_found", "upstream", "network", "timeout"):
                raise
            versions = []
            versions_unavailable = True
        versions = sorted({v for v in versions if v}, reverse=True)
        payload: Dict[str, Any] = {
            "ok": True, "jurisdiction": "EU",
            "law": {"abbreviation": act.get(lang) or act.get("bg") or celex, "celex": celex,
                    "title": facts.get("title", ""), "url": eu_mod.human_url(celex, lang)},
            "status": {"consolidated_as_of": consolidated, "official": False, "official_source": True,
                       "legal_effect": facts.get("legal_effect", ""),
                       "official_journal": facts.get("official_journal", ""),
                       "amended_by": facts.get("amended_by", []),
                       "corrected_by": facts.get("corrected_by", [])},
            "versions": {"total": len(versions), "entries": versions[:limit], "current": doc_id,
                         "complete": not versions_unavailable and len(versions) < 50 and len(versions) <= limit,
                         "note": ("Consolidated versions the Publications Office publishes for this CELEX, "
                                  "newest first. bg_law_get reads the newest one; a date from this list is a "
                                  "real snapshot, unlike an arbitrary as_of date.")},
            "source": self._source_eu(doc_id, consolidated, lang, cached),
        }
        if not versions:
            payload["versions"]["limitation"] = ("The version index could not be checked; absence of snapshots is unconfirmed."
                                                 if versions_unavailable else "No consolidated versions are published for this CELEX; the original text is used.")
        if note:
            payload["version_note"] = note
        stale = self._stale_note(cached)
        if stale:
            payload["stale"] = True
            payload["stale_note"] = stale
        index = getattr(self.eu._local, "versions", None)
        _freshness(payload, cached, *([index] if index else []))
        if versions_unavailable:
            payload.update(stale=True, stale_note="The version index could not be checked.")
        return payload

    # --------------------------------------------------------- bg_law_history
    def law_history(self, args: Dict[str, Any]) -> Dict[str, Any]:
        law = str(args.get("law") or args.get("citation") or "").strip()
        if not law:
            raise LawError("bad_argument", "No act given.", "Pass an act, e.g. 'КТ'.")
        limit = _int(args.get("limit"), 40, 1, 300)
        act_eu = eu_mod.match_eu(law)
        if act_eu:
            if not self._flag("eu_law"):
                raise LawError("disabled", "EU legislation is switched off in settings.")
            return self._eu_history(act_eu, self._eu_lang(args.get("language")), limit)
        if not self._flag("bg_law"):
            raise LawError("disabled", "Bulgarian legislation is switched off in settings.")
        try:
            act_id, title = self.bg.resolve(law)
        except LawError as err:
            if err.kind not in ("law_not_found", "ambiguous_law"):
                raise
            act_eu = eu_mod.match_eu(law)
            if act_eu and self._flag("eu_law"):
                return self._eu_history(act_eu, self._eu_lang(args.get("language")), limit)
            raise
        cached = self.bg.act(act_id)
        data = cached.data
        meta = self.bg.meta(act_id)
        chain = data["meta"].get("gazette_chain") or []
        try:
            history_cached = self.bg.history(act_id)
            hist = history_cached.data
        except LawError as err:
            if err.kind == "not_found":
                hist = {}
                history_cached = None
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
            payload["point_in_time"]["limitation"] = (f"Reliable point-in-time text begins at {coverage.get('reliable_from') or coverage.get('earliest')} "
                                                      f"({coverage.get('count')} indexed versions). Earlier dates may be unavailable; "
                                                      f"never substitute current text.")
        stale = self._stale_note(cached)
        if stale:
            payload["stale"] = True
            payload["stale_note"] = stale
        _freshness(payload, cached, meta, *([history_cached] if history_cached else []))
        return payload

    # --------------------------------------------------------- bg_case_search
    def case_search(self, args: Dict[str, Any]) -> Dict[str, Any]:
        if not self._flag("case_law"):
            raise LawError("disabled", "Case-law lookups are switched off in this plugin's settings (case_law).")
        if args.get("court") == "EU":
            if not self._flag("eu_cases"):
                raise LawError("disabled", "CJEU judgments are switched off in settings.")
            query = str(args.get("query") or "").strip()
            if not query:
                raise LawError("bad_argument", "Give title words for EU court search.")
            cached = self.eu.search(query, _int(args.get("limit"), 10, 1, 50),
                                    _int(args.get("offset"), 0, 0, 1000000), court=True)
            result = {"ok": True, **cached.data,
                      "source": {"url": eu_mod.SPARQL, "license": "CC BY 4.0"}}
            _freshness(result, cached)
            return result
        if not self._flag("bg_cases"):
            raise LawError("disabled", "Constitutional Court acts are switched off in settings.")
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
            if len(dto) == 7:
                from calendar import monthrange
                try:
                    dto = dto + f"-{monthrange(int(dto[:4]), int(dto[5:]))[1]}"
                except ValueError:
                    raise LawError("bad_argument", "Invalid month in date_to.")
            else:
                dto += "-12-31"
        if dfrom and len(dfrom) < 10:
            dfrom += "-01" if len(dfrom) == 7 else "-01-01"
        dfrom, dto = _date(dfrom), _date(dto)
        if dfrom and dto and dfrom > dto:
            raise LawError("bad_argument", "date_from must be on or before date_to.")
        limit = _int(args.get("limit"), 10, 1, 50)
        scan_limit = _int(args.get("scan_limit"), 8, 1, 25)
        scan_offset = _int(args.get("scan_offset"), 0, 0, 1000000)
        rows, scanned, capped = self.cases.search(case_number or None, dfrom or None, dto or None,
                                                 act_type or None, query or None, scan_limit, scan_offset)
        index = self.cases.index()
        payload: Dict[str, Any] = {
            "ok": True, "total_matches": len(rows),
            "results": rows[_int(args.get("offset"), 0, 0, 1000000):][:limit],
            "covers": ("Published acts of the Bulgarian Constitutional Court only: решения, определения, "
                       "становища and особени мнения. The Supreme Courts (ВКС, ВАС), appellate and first-instance "
                       "courts are not covered."),
            "act_types": sorted({row["act_type"] for row in index.data if row.get("act_type")}),
            "source": {"name": "Конституционен съд на Република България (search-acts index)",
                       "url": constcourt.LIST_URL, "license": "public",
                       "attribution": ("Източник: Конституционен съд на Република България "
                                       "(www.constcourt.bg), списък на публикуваните актове."),
                       "retrieved_at": _iso_ts(index.fetched_at),
                       "cache": index.state, "acts_indexed": len(index.data)},
        }
        if query:
            payload["keyword_scan"] = {"acts_downloaded": scanned - len(getattr(self.cases, "last_failures", [])), "capped": capped,
                                       "attempts": scanned, "failures": getattr(self.cases, "last_failures", []),
                                       "next_scan_offset": scan_offset + scanned if capped else None,
                                       "note": ("Keywords are matched inside the acts themselves, so a keyword "
                                                "search downloads the newest candidates up to scan_limit. Narrow "
                                                "with a date or act type, or raise scan_limit.")}
        if not rows:
            payload["message"] = ("No match in the scanned candidates; coverage is incomplete."
                                  if capped or getattr(self.cases, "last_failures", []) else "No published Constitutional Court act matches.")
            payload["next_step"] = ("Widen or narrow the date range, drop the keywords and read by case number, "
                                    "or note that other courts are not covered.")
        else:
            payload["next_step"] = "Read one with bg_case_get(doc_id=...)."
        _freshness(payload, index, *self.cases.scan_copies)
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
            return self._judgment(celex, self._eu_lang(args.get("language")), args.get("paragraph"), max_chars, _int(args.get("offset"), 0, 0, 100000000))
        if not doc_id and case_number and (eu_mod.match_case(case_number)
                                           or re.fullmatch(r"[CTF]-\d+/\d{2,4}", case_number, re.I)):
            # C-311/18 is a Court of Justice case number; Bulgarian numbers look nothing like it.
            return self._judgment(case_number, self._eu_lang(args.get("language")), args.get("paragraph"),
                                  max_chars, _int(args.get("offset"), 0, 0, 100000000))
        if not self._flag("bg_cases"):
            raise LawError("disabled", "Constitutional Court acts are switched off in settings.")
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
        cached = self.cases.act_cached(doc_id)
        act = cached.data
        cut = _cut(act["text"], max_chars, _int(args.get("offset"), 0, 0, 100000000))
        payload: Dict[str, Any] = {
            "ok": True, "jurisdiction": "BG",
            "decision": {"doc_id": f"act-{act['doc_id']}" if act["doc_id"] else doc_id,
                         "act_type": act["act_type"], "date": act["date"],
                         "case_number": act["case_number"], "url": act["url"]},
            "parts": cut, "truncated": cut["truncated"],
        }
        if cut["truncated"]:
            payload["next_step"] = cut["next_step"]
        payload["source"] = {"name": "Конституционен съд на Република България", "url": act["url"],
                             "license": "public", "attribution": self._case_attribution(act),
                             "cache": cached.state, "retrieved_at": _iso_ts(cached.fetched_at)}
        _freshness(payload, cached)
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

    def _judgment(self, name: str, lang: str, paragraph: Any, max_chars: int, offset=0) -> Dict[str, Any]:
        if not self._flag("eu_cases"):
            raise LawError("disabled", "CJEU judgments are switched off in settings.")
        case = eu_mod.match_case(name)
        if not case:
            raise LawError("bad_argument", f"'{name}' is not a CELEX number of a Court of Justice judgment.",
                           "Pass a judgment CELEX like 62018CJ0311, a case number like C-311/18, or a known "
                           "name like 'Schrems II'.")
        celex = case["celex"]
        cached = self.eu.judgment(celex, lang)
        doc = cached.data
        if case.get("requested_case"):
            normalize = lambda t: re.sub(r"\s", "", re.sub(r"[\u2010-\u2015\u2212]", "-", t)).lower()
            if normalize(case["requested_case"]) not in normalize(doc.get("text", "")):
                raise LawError("case_not_found", "The downloaded judgment does not confirm the requested case number.",
                               "Search court=EU for a canonical judgment CELEX, especially for joined cases.")
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
        cut = _cut(text, max_chars, offset)
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
            "parts": cut, "truncated": cut["truncated"],
            "source": {"name": "Publications Office of the EU (CELLAR)", "url": eu_mod.human_url(celex, lang),
                       "data_url": eu_mod.CELLAR + celex, "license": "CC BY 4.0",
                       "retrieved_at": _iso_ts(cached.fetched_at), "cache": cached.state,
                       "attribution": attribution},
        }
        if para:
            payload["paragraph"] = para
        if cut["truncated"]:
            payload["next_step"] = cut["next_step"]
        stale = self._stale_note(cached)
        if stale:
            payload["stale"] = True
            payload["stale_note"] = stale
        return payload


    def _api_result(self, cached, **extra):
        result = {"ok": True, "data": cached.data, **extra,
                  "source": {"name": "Питай законите (lsource.net)",
                             "url": cached.meta.get("url", lsource.API), "license": "CC BY 4.0",
                             "cache": cached.state, "retrieved_at": _iso_ts(cached.fetched_at),
                             "attribution": "Източник: Питай законите (lsource.net), CC BY 4.0; реконструкция от Държавен вестник."}}
        _freshness(result, cached)
        return result

    def law_changes(self, args):
        if not self._flag("bg_law"):
            raise LawError("disabled", "Bulgarian legislation is switched off in settings.")
        act_id, _ = self.bg.resolve(str(args.get("law") or ""))
        revision = str(args.get("revision") or "")
        if revision:
            if not re.fullmatch(r"[a-fA-F0-9]{6,64}", revision):
                raise LawError("bad_argument", "Pass a revision hash from bg_law_history.")
            cached = self.bg.endpoint(f"acts/{lsource._q(act_id)}/diff/{revision}")
        else:
            start, end = _date(args.get("date_from")), _date(args.get("date_to"))
            if not start or not end or start > end:
                raise LawError("bad_argument", "Give date_from and date_to in chronological order.")
            cached = self.bg.endpoint(f"acts/{lsource._q(act_id)}/changes", **{"from": start, "to": end})
        data = dict(cached.data)
        if isinstance(data.get("diff"), str):
            data["diff_page"] = _cut(data.pop("diff"), _int(args.get("max_chars"), 10000, 500, 60000),
                                     _int(args.get("offset"), 0, 0, 100000000))
        result = self._api_result(cached, law=act_id,
                                  note="Resolved from/to dates in data are the source's actual coverage, not necessarily the requested bounds.")
        result["data"] = data
        result["source"]["url"] = lsource.public_url(act_id)
        return result

    def law_transposition(self, args):
        if not self._flag("bg_law"):
            raise LawError("disabled", "Bulgarian legislation is switched off in settings.")
        if args.get("law"):
            act_id, _ = self.bg.resolve(str(args["law"]))
            cached = self.bg.endpoint(f"acts/{lsource._q(act_id)}/transposition")
        else:
            cached = self.bg.endpoint("transposition", q=str(args.get("query") or ""),
                                      limit=_int(args.get("limit"), 25, 1, 500),
                                      offset=_int(args.get("offset"), 0, 0, 1000000))
        return self._api_result(cached, note="Relationships are claims extracted from the acts' own sentences; they do not establish complete or correct transposition.")

    def law_recent(self, args):
        if not self._flag("bg_law"):
            raise LawError("disabled", "Bulgarian legislation is switched off in settings.")
        cached = self.bg.endpoint("acts/recent")
        result = self._api_result(cached)
        rows = cached.data.get("recent", [])
        offset, limit = _int(args.get("offset"), 0, 0, 1000000), _int(args.get("limit"), 25, 1, 100)
        result["data"] = {"recent": rows[offset:offset + limit], "available": len(rows), "offset": offset,
                          "next_offset": offset + limit if offset + limit < len(rows) else None,
                          "note": "A bounded recent feed, not a complete historical corpus search."}
        return result

    def law_status(self, args):
        from .settings import probe, status
        return {"ok": True, **(probe(self.store) if args.get("check_sources") is True else status(self.store))}

    def law_export(self, args):
        if not self._flag("bg_law"):
            raise LawError("disabled", "Bulgarian legislation is switched off in settings.")
        act_id, title = self.bg.resolve(str(args.get("law") or ""))
        as_of = _date(args.get("as_of"))
        fmt = args.get("format", "akn")
        if fmt == "akn":
            cached = self.bg.akn(act_id, as_of)
            text = cached.data["xml"]
        elif fmt == "text":
            suffix = "?as_of=" + lsource._q(as_of) if as_of else ""
            cached = self.bg._get(f"export-text-{act_id}{suffix}", f"{lsource.API}/acts/{lsource._q(act_id)}/text{suffix}")
            text = cached.data.get("text", "")
        else:
            raise LawError("bad_argument", "Export format must be akn or text.")
        if not text.strip():
            raise LawError("bad_response", "The source returned an empty export.")
        result = self._api_result(cached, law=act_id, format=fmt,
                                  document=_cut(text, _int(args.get("max_chars"), 30000, 500, 60000),
                                                _int(args.get("offset"), 0, 0, 100000000)))
        result.pop("data", None)
        result["source"]["url"] = lsource.public_url(act_id)
        return result


def _as_handler(fn: Callable[[Dict[str, Any]], Dict[str, Any]], name: str) -> Callable:
    def handler(args: Optional[dict] = None, **_kwargs) -> str:
        try:
            if not isinstance(args, dict):
                args = {}
            payload = fn(dict(args))
            if isinstance(payload, dict) and payload.get("ok") and "disclaimer" not in payload:
                payload["disclaimer"] = DISCLAIMER
            return _dump(payload)
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
        "bg_law_changes": _as_handler(t.law_changes, "bg_law_changes"),
        "bg_law_transposition": _as_handler(t.law_transposition, "bg_law_transposition"),
        "bg_law_recent": _as_handler(t.law_recent, "bg_law_recent"),
        "bg_law_status": _as_handler(t.law_status, "bg_law_status"),
        "bg_law_export": _as_handler(t.law_export, "bg_law_export"),
    }
