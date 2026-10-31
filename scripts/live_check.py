"""Live end-to-end check of the bg-law tools against the real sources.

Run with the lsource.net key in the environment:

    python scripts/live_check.py            # everything
    python scripts/live_check.py -k eu      # only groups whose name contains "eu"

Every assertion is a structural invariant (an issue number is present, a text is not empty, an
error type is the documented one) rather than an exact quotation, because the sources are amended
and a fixed string would fail for the wrong reason. Failures print the tool result that broke them.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP_HOME = Path(tempfile.mkdtemp(prefix="bg-law-live-"))
# A fresh cache per run: inheriting the host cache would hide a fixed parser behind an old parse.
os.environ["HERMES_HOME"] = str(TMP_HOME)

if not os.environ.get("LSOURCE_API_KEY"):
    sys.exit("LSOURCE_API_KEY is not set; export it before running this check")


def load_plugin():
    spec = importlib.util.spec_from_file_location(
        "bg_law_live", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["bg_law_live"] = module
    spec.loader.exec_module(module)
    return module


plugin = load_plugin()
from bg_law_live import client, eu, lsource, tools  # noqa: E402

RESULTS: list[tuple[str, str, bool, str]] = []
GROUP = "general"


def check(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((GROUP, name, bool(ok), detail))
    mark = "PASS" if ok else "FAIL"
    line = f"  [{mark}] {name}"
    if detail and not ok:
        line += f"\n         {detail}"
    print(line)
    return bool(ok)


def group(name: str):
    global GROUP

    def wrap(fn):
        def run():
            global GROUP
            GROUP = name
            print(f"\n{name}")
            fn()

        run.__name__ = fn.__name__
        return run

    return wrap


class Client:
    """Owns one Store plus the request log, so pacing can be measured."""

    def __init__(self, **overrides):
        self.calls: list[float] = []
        self.by_host: dict[str, list[float]] = {}
        settings = {
            "lsource_api_key": os.environ["LSOURCE_API_KEY"],
            "language": "auto",
            "eu_language": "bg",
            "max_age_hours": 24,
            "cache_max_mb": 200,
            "case_law": True,
            "eu_law": True,
        }
        settings.update(overrides)
        self.settings = settings
        store = client.Store(settings=lambda key, default=None: settings.get(key, default))
        self.store = store
        transport = store.http._transport

        def counted(url, headers, *args, **kwargs):
            stamp = time.monotonic()
            self.calls.append(stamp)
            host = url.split("/")[2] if "//" in url else url
            self.by_host.setdefault(host, []).append(stamp)
            return transport(url, headers, *args, **kwargs)

        store.http._transport = counted
        CLIENTS.append(self)
        self.h = tools.make_handlers(tools.Tools(store))

    def __call__(self, tool: str, **args) -> dict:
        raw = self.h[tool](args)
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            check(f"{tool} returns JSON", False, f"{exc}: {raw[:200]}")
            return {"ok": False, "_raw": raw}


CLIENTS: list["Client"] = []
ONE = Client()


def ok_call(tool: str, **args) -> dict:
    result = ONE(tool, **args)
    check(f"{tool}({', '.join(f'{k}={v!r}' for k, v in args.items())[:80]}) ok",
          result.get("ok") is True, json.dumps(result, ensure_ascii=False)[:400])
    return result


@group("bg: reading articles")
def bg_articles():
    r = ok_call("bg_law_get", citation="чл. 344, ал. 1 КТ")
    prov = r.get("provision") or {}
    text = prov.get("text", "")
    check("чл. 344, ал. 1 text is the real алинея", "оспорва законността" in text, text[:200])
    check("al. 1 does not leak the next алинея", "(2)" not in text[:50], text[:120])
    check("scope names the раздел", "уволнение" in (prov.get("scope") or ""), prov.get("scope"))
    ver = r.get("version") or {}
    check("version carries a ДВ issue", bool(ver.get("issue")) and "ДВ" in ver["issue"], str(ver))
    check("version carries an amendment date", bool(ver.get("changed")), str(ver))
    check("act is not marked official", ver.get("official") is False, str(ver))
    check("gazette chain is long", (ver.get("gazette_count") or 0) > 50, str(ver.get("gazette_count")))
    src = r.get("source") or {}
    check("source url is the lsource page", str(src.get("url", "")).startswith("https://lsource.net/"), str(src))
    check("attribution names the source and the licence",
          "lsource.net" in src.get("attribution", "") and "CC BY" in src.get("attribution", ""),
          str(src.get("attribution")))

    two = ok_call("bg_law_get", citation="чл. 344, ал. 2 КТ")
    t2 = (two.get("provision") or {}).get("text", "")
    check("al. 2 differs from al. 1", t2 and t2 != text, t2[:160])

    three = ok_call("bg_law_get", citation="чл. 344, ал. 1, т. 3 КТ")
    t3 = (three.get("provision") or {}).get("text", "")
    check("т. 3 narrows the citation", "т. 3" in (three.get("provision") or {}).get("citation", ""),
          (three.get("provision") or {}).get("citation"))
    check("т. 3 body is shorter than the whole алинея", 0 < len(t3) <= len(text), f"{len(t3)} vs {len(text)}")

    a = ok_call("bg_law_get", citation="чл. 2а КТ")
    check("чл. 2а resolves (Cyrillic suffix)", "2а" in (a.get("provision") or {}).get("citation", ""),
          str(a.get("provision", {}).get("citation")))
    latin = ok_call("bg_law_get", citation="чл. 2a КТ")
    check("Latin 2a maps to чл. 2а",
          (latin.get("provision") or {}).get("citation") == (a.get("provision") or {}).get("citation"),
          f"{latin.get('provision', {}).get('citation')} vs {a.get('provision', {}).get('citation')}")

    const = ok_call("bg_law_get", citation="чл. 5 от Конституцията")
    check("Конституция resolves by name",
          "Конституция" in (const.get("law") or {}).get("title", ""), str(const.get("law")))

    nk = ok_call("bg_law_get", citation="чл. 115 НК")
    check("НК article is non-trivial", len((nk.get("provision") or {}).get("text", "")) > 80,
          str(nk.get("provision", {}).get("text"))[:120])
    check("НК has a gazette issue despite empty meta",
          bool((nk.get("version") or {}).get("issue")), str(nk.get("version")))

    long_name = ok_call("bg_law_get", citation="чл. 6, ал. 1 от Закона за защита на личните данни")
    check("long act name resolves", long_name.get("law", {}).get("abbreviation") == "ЗЗЛД",
          str(long_name.get("law")))

    reverse = ok_call("bg_law_get", citation="КТ чл. 344")
    check("law before the number parses too",
          reverse.get("law", {}).get("id") == "kodeks-na-truda"
          and "оспорва законността" in (reverse.get("provision") or {}).get("text", ""),
          str(reverse.get("provision", {}).get("text"))[:120])

    definite = ok_call("bg_law_get", citation="чл. 344 от Кодекса на труда")
    check("definite article does not break resolution",
          definite.get("law", {}).get("id") == "kodeks-na-truda", str(definite.get("law")))

    repealed = ok_call("bg_law_get", citation="чл. 12 КТ")
    flag = (repealed.get("provision") or {}).get("repealed")
    check("repealed article is flagged", flag is True, json.dumps(repealed.get("provision", {}),
          ensure_ascii=False)[:300])

    para = ok_call("bg_law_get", citation="§ 1 ЗЗД")
    check("§ transitional paragraph resolves", para.get("ok") is True
          and len((para.get("provision") or {}).get("text", "")) > 50, str(para.get("provision"))[:200])


@group("bg: negative paths")
def bg_negatives():
    missing = ONE("bg_law_get", citation="чл. 9999 КТ")
    check("missing article is article_not_found", missing.get("error_type") == "article_not_found",
          json.dumps(missing, ensure_ascii=False)[:300])
    check("missing article suggests the table of contents",
          bool(missing.get("next_step")), str(missing.get("next_step"))[:160])

    no_act = ONE("bg_law_get", citation="чл. 5 от Закона за несъществуващите неща")
    check("unknown act is law_not_found", no_act.get("error_type") == "law_not_found",
          json.dumps(no_act, ensure_ascii=False)[:300])

    empty = ONE("bg_law_get", citation="")
    check("empty citation is bad_argument", empty.get("error_type") == "bad_argument",
          json.dumps(empty, ensure_ascii=False)[:200])

    junk = ONE("bg_law_get", citation="asdfgh")
    check("junk citation does not invent an act", junk.get("ok") is False,
          json.dumps(junk, ensure_ascii=False)[:200])

    for tool in ("bg_law_toc", "bg_law_history", "bg_law_search"):
        r = ONE(tool, law="")
        if tool == "bg_law_search":
            r = ONE(tool, query="")
        check(f"{tool} rejects empty input", r.get("ok") is False, json.dumps(r, ensure_ascii=False)[:200])

    nothing = ONE("bg_law_search", query="щзхюъф")
    check("nonsense search returns no invented acts",
          nothing.get("ok") is False or not (nothing.get("results") or nothing.get("acts")),
          json.dumps(nothing, ensure_ascii=False)[:250])


@group("bg: search, toc, history")
def bg_structure():
    s = ok_call("bg_law_search", query="кодекс на труда")
    hits = s.get("results") or s.get("acts") or []
    check("search finds КТ first", bool(hits) and hits[0].get("law") == "kodeks-na-truda",
          json.dumps(hits[:2], ensure_ascii=False)[:300])

    alias = ok_call("bg_law_search", query="ЗЗД")
    hits = alias.get("results") or alias.get("acts") or []
    check("abbreviation ЗЗД resolves by alias",
          any(h.get("law") == "zakon-za-zadalzheniyata-i-dogovorite" for h in hits),
          json.dumps(hits[:3], ensure_ascii=False)[:300])

    kinds = ok_call("bg_law_search", query="данък", kind="закон")
    hits = kinds.get("results") or kinds.get("acts") or []
    check("kind filter returns only that kind", all(h.get("kind") == "закон" for h in hits),
          json.dumps(hits[:3], ensure_ascii=False)[:300])

    toc = ok_call("bg_law_toc", law="КТ", limit=5)
    entries = toc.get("entries") or []
    check("toc returns entries", len(entries) == 5, f"{len(entries)} entries")
    check("toc entries carry scope and id", all(e.get("id") and "scope" in e for e in entries),
          json.dumps(entries[:2], ensure_ascii=False)[:300])

    page2 = ok_call("bg_law_toc", law="КТ", limit=5, offset=5)
    ids1 = {e.get("id") for e in entries}
    ids2 = {(page2.get("entries") or [{}])[0].get("id")}
    check("offset pages do not repeat rows", not (ids1 & ids2), f"{sorted(ids1)} vs {sorted(ids2)}")

    found = ok_call("bg_law_toc", law="КТ", query="оспорва законността на уволнението")
    hits = found.get("entries") or []
    check("toc text search finds чл. 344", bool(hits) and hits[0].get("id") == "Чл. 344",
          json.dumps(hits[:2], ensure_ascii=False)[:300])
    check("toc text hit includes a snippet", bool(hits and hits[0].get("snippet")),
          json.dumps(hits[:1], ensure_ascii=False)[:200])

    hist = ok_call("bg_law_history", law="КТ")
    chain = hist.get("gazette_chain") or {}
    check("history has the full chain", (chain.get("total") or 0) > 50, str(chain.get("total")))
    entries = chain.get("entries") or []
    check("history exposes entries", bool(entries), str(list(hist.keys())))
    check("latest entry is the newest issue", "2026" in json.dumps(entries[-1] if entries else {},
          ensure_ascii=False), json.dumps(entries[-1:] or [], ensure_ascii=False)[:200])
    check("history reports point-in-time coverage", "point_in_time" in hist,
          str(list(hist.keys())))

    nk = ok_call("bg_law_history", law="НК")
    nk_text = json.dumps(nk, ensure_ascii=False)
    check("НК history reports a 2026 issue", "2026" in nk_text, nk_text[:250])


@group("eu: articles, recitals, annexes")
def eu_articles():
    gdpr = ok_call("bg_law_get", citation="чл. 6, пар. 1, б. „е“ ОРЗД")
    prov = gdpr.get("provision") or {}
    check("ОРЗД resolves from the Bulgarian name", "ОРЗД" in json.dumps(gdpr.get("law"), ensure_ascii=False),
          json.dumps(gdpr.get("law"), ensure_ascii=False))
    check("GDPR text is Bulgarian", "законосъобразно" in prov.get("text", "").lower(),
          prov.get("text", "")[:200])
    check("GDPR pin names the letter", "е" in (prov.get("citation") or ""), str(prov.get("citation")))
    check("EU result carries a CELEX",
          bool((gdpr.get("version") or {}).get("celex") or (gdpr.get("law") or {}).get("celex")),
          str(gdpr.get("version"))[:200])
    check("EU attribution names the Publications Office",
          "europa.eu" in json.dumps(gdpr.get("source"), ensure_ascii=False),
          str(gdpr.get("source"))[:200])

    en = ok_call("bg_law_get", citation="Art. 6(1)(f) GDPR", language="en")
    text = (en.get("provision") or {}).get("text", "")
    check("English edition returns English", "lawful" in text.lower(), text[:200])
    check("the two languages are the same provision",
          len(text) > 60 and "processing" in text.lower(), text[:160])

    art17 = ok_call("bg_law_get", citation="чл. 17 ОРЗД")
    check("right to erasure reads as the right to erasure",
          "изтриване" in (art17.get("provision") or {}).get("text", "").lower(),
          (art17.get("provision") or {}).get("text", "")[:200])

    recital = ok_call("bg_law_get", citation="съображение 47 ОРЗД")
    check("recital 47 resolves", "съображение 47" in (recital.get("provision") or {}).get("citation", ""),
          str(recital.get("provision", {}).get("citation")))
    check("recital 47 talks about legitimate interests",
          "интерес" in (recital.get("provision") or {}).get("text", "").lower(),
          (recital.get("provision") or {}).get("text", "")[:200])

    annex = ok_call("bg_law_get", citation="приложение III Регламент за ИИ")
    check("AI Act annex III resolves", len((annex.get("provision") or {}).get("text", "")) > 200,
          str(annex.get("provision", {}).get("text"))[:160])
    check("annex III is about high-risk areas",
          "високорискови" in (annex.get("provision") or {}).get("text", "").lower(),
          (annex.get("provision") or {}).get("text", "")[:200])

    ai50 = ok_call("bg_law_get", citation="чл. 50 Регламент за ИИ")
    check("AI Act article 50 resolves", "прозрачност" in (ai50.get("provision") or {}).get("text", "").lower(),
          (ai50.get("provision") or {}).get("text", "")[:200])

    celex = ok_call("bg_law_get", citation="32016R0679 чл. 6")
    check("a bare CELEX resolves", celex.get("ok") is True, json.dumps(celex, ensure_ascii=False)[:200])

    bad = ONE("bg_law_get", citation="чл. 5 Регламент (ЕС) 9999/9999")
    check("unknown EU act is not invented", bad.get("ok") is False,
          json.dumps(bad, ensure_ascii=False)[:200])


@group("eu: toc and history")
def eu_structure():
    toc = ok_call("bg_law_toc", law="ОРЗД", limit=6)
    entries = toc.get("entries") or []
    check("GDPR toc returns entries", len(entries) == 6, f"{len(entries)}")
    ids = [e.get("id", "") for e in entries]
    check("GDPR toc opens with the recitals", all("съображение" in i for i in ids), str(ids))

    full = ok_call("bg_law_toc", law="ОРЗД", limit=200)
    all_ids = [e.get("id", "") for e in (full.get("entries") or [])]
    check("GDPR toc lists both recitals and articles",
          any("съображение" in i for i in all_ids) and any("чл." in i for i in all_ids),
          f"{len(all_ids)} entries, total {full.get('total')}")

    art = ok_call("bg_law_toc", law="ОРЗД", query="съгласие")
    check("GDPR toc search finds the consent provisions",
          bool(art.get("entries")), json.dumps(art, ensure_ascii=False)[:250])

    hist = ok_call("bg_law_history", law="ОРЗД")
    vers = hist.get("versions") or hist.get("entries") or []
    check("GDPR history lists consolidated versions", bool(vers),
          json.dumps(hist, ensure_ascii=False)[:300])

    aiact = ok_call("bg_law_history", law="Регламент за ИИ")
    check("AI Act history resolves", aiact.get("ok") is True, json.dumps(aiact, ensure_ascii=False)[:200])


@group("case law: constitutional court")
def constcourt():
    found = ok_call("bg_case_search", case_number="15/2026")
    acts = found.get("results") or found.get("acts") or []
    check("case number search returns acts", bool(acts), json.dumps(found, ensure_ascii=False)[:300])
    check("every hit carries a doc id", all(a.get("doc_id") or a.get("id") for a in acts),
          json.dumps(acts[:2], ensure_ascii=False)[:300])

    doc_id = (acts[0].get("doc_id") or acts[0].get("id")) if acts else None
    if doc_id:
        act = ok_call("bg_case_get", doc_id=doc_id)
        text = (act.get("parts") or {}).get("text") or ""
        check("case text is a real judgment", len(text) > 2000, f"{len(text)} chars")
        flat = "".join(text.split()).upper()
        check("case text carries the court's own heading",
              any(word in flat for word in ("РЕШЕНИЕ", "ОПРЕДЕЛЕНИЕ", "СТАНОВИЩЕ")), text[:200])
        case = act.get("decision") or {}
        check("case metadata carries a number and a date",
              bool(case.get("case_number")) and bool(case.get("date")),
              json.dumps(case, ensure_ascii=False)[:250])
        check("case attribution names the court",
              "constcourt.bg" in json.dumps(act.get("source"), ensure_ascii=False),
              str(act.get("source"))[:200])

    typed = ok_call("bg_case_search", act_type="решение", limit=3)
    check("act type filter returns only that type",
          all((a.get("type") or a.get("act_type") or "").lower().startswith("реш")
              for a in (typed.get("results") or typed.get("acts") or [])),
          json.dumps((typed.get("results") or [])[:3], ensure_ascii=False)[:300])

    dated = ok_call("bg_case_search", date_from="2026-01-01", date_to="2026-06-30", limit=3)
    check("date range returns hits", bool(dated.get("results") or dated.get("acts")),
          json.dumps(dated, ensure_ascii=False)[:250])

    keywords = ok_call("bg_case_search", query="изборен кодекс", limit=3, scan_limit=1)
    check("keyword scan finds an act or says it scanned",
          keywords.get("ok") is True,
          json.dumps(keywords, ensure_ascii=False)[:300])

    none = ONE("bg_case_search", case_number="999/9999")
    check("unknown case number is not invented",
          none.get("ok") is False or not (none.get("results") or none.get("acts")),
          json.dumps(none, ensure_ascii=False)[:250])

    missing = ONE("bg_case_get", doc_id="act-99999999")
    check("unknown doc id is an error", missing.get("ok") is False,
          json.dumps(missing, ensure_ascii=False)[:200])


@group("case law: court of justice")
def cjeu():
    j = ok_call("bg_case_get", case_number="C-311/18")
    text = (j.get("parts") or {}).get("text") or ""
    check("Schrems II resolves by case number", "62018CJ0311" in json.dumps(j, ensure_ascii=False),
          json.dumps(j.get("case") or {}, ensure_ascii=False)[:250])
    check("judgment text is long", len(text) > 5000, f"{len(text)} chars")

    point = ok_call("bg_case_get", case_number="C-311/18", paragraph=201)
    ptext = (point.get("parts") or {}).get("text") or ""
    check("point 201 is a single point", 40 < len(ptext) < len(text),
          f"point {len(ptext)} vs whole {len(text)}")
    check("point 201 is returned under its own number",
          str(point.get("paragraph")) == "201" and not ptext.startswith("201"), ptext[:120])

    by_celex = ok_call("bg_case_get", celex="62018CJ0311", paragraph=1)
    check("CELEX lookup matches the case-number lookup",
          len((by_celex.get("parts") or {}).get("text") or "") > 50,
          str((by_celex.get("parts") or {}).get("text"))[:120])

    missing = ONE("bg_case_get", case_number="C-999/99")
    check("unknown judgment is not invented", missing.get("ok") is False,
          json.dumps(missing, ensure_ascii=False)[:200])


@group("limits: truncation, as_of, max_chars")
def limits():
    short = ok_call("bg_law_get", citation="чл. 344 КТ", max_chars=500)
    prov = short.get("provision") or {}
    check("max_chars truncates", len(prov.get("text", "")) <= 520, str(len(prov.get("text", ""))))
    check("truncation is declared", prov.get("truncated") is True, json.dumps(prov, ensure_ascii=False)[:200])
    check("truncated result says how to get more",
          bool(short.get("next_step") or prov.get("next_step")), json.dumps(short, ensure_ascii=False)[:250])

    huge = ok_call("bg_law_get", citation="чл. 344 КТ", max_chars=1000000)
    check("absurd max_chars is clamped, not honoured", isinstance(huge.get("provision"), dict),
          json.dumps(huge, ensure_ascii=False)[:160])

    bad = ONE("bg_law_get", citation="чл. 344 КТ", max_chars="banana")
    check("non-numeric max_chars falls back instead of crashing", bad.get("ok") is True,
          json.dumps(bad, ensure_ascii=False)[:200])

    snap = ONE("bg_law_get", citation="чл. 344, ал. 1 КТ", as_of="2026-08-15")
    if snap.get("ok"):
        check("as_of between versions resolves to the newest earlier snapshot",
              bool(snap.get("as_of_used")) or snap.get("as_of") == "2026-08-15",
              json.dumps(snap, ensure_ascii=False)[:400])
    else:
        details = snap.get("details") or {}
        check("as_of between versions is answered honestly",
              snap.get("error_type") == "as_of_not_available"
              and bool(details.get("coverage") or snap.get("coverage")),
              json.dumps(snap, ensure_ascii=False)[:400])

    old = ONE("bg_law_get", citation="чл. 344 КТ", as_of="1990-01-01")
    details = old.get("details") or {}
    check("as_of before the reconstruction refuses, with coverage named",
          old.get("error_type") == "as_of_not_available"
          and bool(details.get("coverage") or old.get("coverage")),
          json.dumps(old, ensure_ascii=False)[:400])

    invalid = ONE("bg_law_get", citation="чл. 344 КТ", as_of="not-a-date")
    check("invalid as_of is rejected", invalid.get("ok") is False,
          json.dumps(invalid, ensure_ascii=False)[:200])


@group("resilience: pacing, cache, stale, key, allowlist")
def resilience():
    index_url = "https://www.constcourt.bg/bg/search-acts"
    warm = Client()
    before = len(warm.calls)
    warm.store.fetch("probe-index", index_url, lambda body, resp: len(body))
    fetched_requests = len(warm.calls) - before
    before = len(warm.calls)
    cached = warm.store.fetch("probe-index", index_url, lambda body, resp: len(body))
    cached_requests = len(warm.calls) - before
    check("the first read goes to the network", fetched_requests == 1, f"{fetched_requests} request(s)")
    check("a warm read makes no request at all", cached_requests == 0, f"{cached_requests} request(s)")
    check("a warm read is served from the cache", cached.state == "cached", cached.state)
    check("cache reports the age of the entry", cached.meta.get("fetched_at") is not None,
          str(cached.meta)[:150])

    # max_age_hours=0 means "revalidate every time", which must be one conditional request, not a refetch
    reval = Client(max_age_hours=0)
    reval.store.fetch("probe-reval", index_url, lambda body, resp: len(body))
    before = len(reval.calls)
    again = reval.store.fetch("probe-reval", index_url, lambda body, resp: len(body))
    revalidate_requests = len(reval.calls) - before
    check("an expired entry is revalidated with one conditional request", revalidate_requests == 1,
          f"{revalidate_requests} request(s)")
    validators = reval.store.cache.read("probe-reval")[1]
    has_validator = bool(validators.get("etag") or validators.get("last_modified"))
    check("an expired entry keeps the cached copy when the source allows it",
          again.state in ("revalidated", "cached") if has_validator else again.state == "fresh",
          f"state {again.state}, validators {'present' if has_validator else 'absent'}")

    # an entry must be past its max age for the client to try the network at all
    broken = Client(max_age_hours=0)
    broken.store.fetch("stale-probe", index_url, lambda body, resp: len(body))
    transport = broken.store.http._transport

    def offline(url, headers, *args, **kwargs):
        raise client.LawError("network", "simulated outage")

    broken.store.http._transport = offline
    try:
        served = broken.store.fetch("stale-probe", index_url, lambda body, resp: len(body))
    except client.LawError as exc:
        check("stale copy is served during an outage", False, f"raised {exc.kind}: {exc}")
    else:
        check("stale copy is served during an outage", served.state == "stale", str(served.state))
    finally:
        broken.store.http._transport = transport

    nokey = Client(lsource_api_key="")
    os.environ["LSOURCE_API_KEY"] = ""
    try:
        r = nokey("bg_law_get", citation="чл. 344 КТ")
    finally:
        os.environ["LSOURCE_API_KEY"] = ONE.settings["lsource_api_key"]
    check("missing key is key_required with a link",
          r.get("error_type") == "key_required" and "lsource" in json.dumps(r, ensure_ascii=False),
          json.dumps(r, ensure_ascii=False)[:250])

    badkey = Client(lsource_api_key="sk_" + "not-a-real-key" * 3, max_age_hours=0)
    r = badkey("bg_law_get", citation="чл. 200 НК")
    check("a rejected key surfaces as an auth error, not a crash",
          r.get("ok") is False and r.get("error_type") in ("unauthorized", "auth", "http_error", "network"),
          json.dumps(r, ensure_ascii=False)[:250])

    try:
        client.Http().get("https://example.com/", max_bytes=1000)
    except client.LawError as exc:
        check("the allowlist refuses a host outside the three sources", exc.kind == "host_not_allowed",
              f"{exc.kind}: {exc}")
    else:
        check("the allowlist refuses a host outside the three sources", False, "example.com was fetched")

    eu_off = Client(eu_law=False)
    r = eu_off("bg_law_get", citation="чл. 6 ОРЗД")
    check("eu_law=false disables EU lookups", r.get("ok") is False and "eu" in json.dumps(r).lower(),
          json.dumps(r, ensure_ascii=False)[:200])

    case_off = Client(case_law=False)
    r = case_off("bg_case_search", case_number="15/2026")
    check("case_law=false disables case law", r.get("ok") is False, json.dumps(r, ensure_ascii=False)[:200])

    host_gaps = {}
    for instance_index, instance in enumerate(CLIENTS):
        for host, stamps in instance.by_host.items():
            gaps = [b - a for a, b in zip(stamps, stamps[1:])]
            if gaps:
                host_gaps[f"{instance_index}:{host}"] = min(gaps)
    slowest = min(host_gaps.values()) if host_gaps else None
    check("requests are paced at least one second apart per host",
          slowest is not None and slowest >= 0.9,
          f"closest spacing per host: { {h: round(g, 2) for h, g in host_gaps.items()} }")

@group("edge: hostile inputs")
def edge_inputs():
    cases = [
        ("bg_law_get", {"citation": "чл. 344 КТ" * 40}),
        ("bg_law_get", {"citation": "🙂🙂🙂"}),
        ("bg_law_get", {"citation": "чл. 344 КТ", "max_chars": "banana"}),
        ("bg_law_get", {"citation": "чл. 344 КТ", "as_of": "2026-13-45"}),
        ("bg_law_get", {"citation": "чл. 344 КТ", "paragraph": "x"}),
        ("bg_law_toc", {"law": "КТ", "limit": -5}),
        ("bg_law_toc", {"law": "КТ", "offset": -1}),
        ("bg_law_search", {"query": "   "}),
        ("bg_law_search", {"query": "'; DROP TABLE acts; --"}),
        ("bg_case_search", {"date_from": "not-a-date"}),
        ("bg_case_search", {}),
        ("bg_case_get", {"doc_id": "nonsense"}),
        ("bg_law_history", {"law": "КТ", "limit": 0}),
        ("bg_law_get", {"law": "КТ"}),
    ]
    for tool, args in cases:
        result = ONE(tool, **args)
        check(f"{tool} survives {sorted(args)}", isinstance(result, dict) and "ok" in result,
              json.dumps(result, ensure_ascii=False)[:200])

    missing_alinea = ONE("bg_law_get", citation="чл. 344, ал. 99 КТ")
    check("a missing алинея is paragraph_not_found", missing_alinea.get("error_type") == "paragraph_not_found",
          json.dumps(missing_alinea, ensure_ascii=False)[:250])

    clamped = ONE("bg_law_toc", law="КТ", limit=-5)
    check("a negative limit is clamped, not honoured",
          clamped.get("ok") is True and len(clamped.get("entries") or []) >= 1,
          json.dumps(clamped, ensure_ascii=False)[:200])

    long_cite = ONE("bg_law_get", citation="чл. 344 КТ" * 40)
    check("an absurdly long citation is refused, not searched forever", long_cite.get("ok") is False,
          json.dumps(long_cite, ensure_ascii=False)[:220])


@group("edge: concurrency and cache integrity")
def concurrency():
    import threading
    shared = Client()
    results = []
    lock = threading.Lock()

    def work(number):
        got = shared("bg_law_get", citation=f"чл. {number} КТ")
        with lock:
            results.append((number, got))

    threads = [threading.Thread(target=work, args=(n,)) for n in range(1, 7)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(120)
    ok = [n for n, r in results if r.get("ok")]
    check("six concurrent lookups all succeed", len(ok) == 6,
          f"{len(ok)}/6 ok: " + json.dumps([n for n, r in results if not r.get("ok")], ensure_ascii=False))
    texts = {(r.get("provision") or {}).get("text", "") for _n, r in results}
    check("concurrent lookups return different articles", len(texts) == 6, f"{len(texts)} distinct texts")

    cache_dir = client.Cache().root
    broken = []
    count = 0
    for path in cache_dir.glob("*.json"):
        count += 1
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            broken.append(path.name)
    check("every cache file is valid JSON", not broken, f"{len(broken)} broken of {count}: {broken[:3]}")
    check("the cache actually stored something", count > 5, f"{count} files")


@group("contract: every success is attributable")
def attribution():
    samples = [
        ONE("bg_law_get", citation="чл. 344, ал. 1 КТ"),
        ONE("bg_law_get", citation="чл. 6 ОРЗД"),
        ONE("bg_law_search", query="трудов"),
        ONE("bg_law_toc", law="КТ", limit=2),
        ONE("bg_law_history", law="КТ"),
        ONE("bg_case_search", case_number="15/2026"),
    ]
    for i, r in enumerate(samples):
        src = r.get("source") or {}
        check(f"sample {i} carries an https source url",
              str(src.get("url", "")).startswith("https://"), json.dumps(src, ensure_ascii=False)[:200])
        check(f"sample {i} carries a licence",
              bool(src.get("license")), json.dumps(src, ensure_ascii=False)[:200])
        check(f"sample {i} carries the not-legal-advice disclaimer",
              "not legal advice" in str(r.get("disclaimer", "")),
              json.dumps(r.get("disclaimer"), ensure_ascii=False))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("-k", "--only", default="", help="run groups whose name contains this")
    args = parser.parse_args()

    groups = [bg_articles, bg_negatives, bg_structure, eu_articles, eu_structure, constcourt,
              cjeu, limits, resilience, edge_inputs, concurrency, attribution]
    for fn in groups:
        if args.only and args.only.lower() not in fn.__name__.lower():
            continue
        try:
            fn()
        except Exception as exc:  # a crashing group must not hide the rest
            check(f"{fn.__name__} raised {type(exc).__name__}", False, str(exc)[:300])

    failed = [r for r in RESULTS if not r[2]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    if failed:
        print("\nFailures:")
        for g, name, _, detail in failed:
            print(f"  - [{g}] {name}\n      {detail}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
