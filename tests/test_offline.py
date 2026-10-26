"""Offline tests for bg-law. No network: every request goes to a fake transport.

Run:  python -m unittest discover -s tests -v      (from the plugin folder; Python 3.11+)

The fixtures under tests/fixtures/ are verbatim excerpts of real responses from
lsource.net, the Publications Office of the EU (CELLAR) and constcourt.bg, captured while
the plugin was built. Trimming them keeps the real shapes without a network dependency.
"""

from __future__ import annotations

import atexit
import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
FIX = Path(__file__).resolve().parent / "fixtures"
_spec = importlib.util.spec_from_file_location("bg_law_plugin", ROOT / "__init__.py",
                                               submodule_search_locations=[str(ROOT)])
pkg = importlib.util.module_from_spec(_spec)
sys.modules["bg_law_plugin"] = pkg
_spec.loader.exec_module(pkg)

from bg_law_plugin import citations, client, constcourt, eu, lsource, schemas, tools  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="bg-law-test-"))
atexit.register(shutil.rmtree, TMP, True)


def fixture(name: str) -> bytes:
    return (FIX / name).read_bytes()


def payload(name: str) -> dict:
    return json.loads(fixture(name).decode("utf-8"))


# ------------------------------------------------------------------ fake web

class FakeWeb:
    """Maps URL patterns to (status, body, headers). Records every request."""

    def __init__(self):
        self.calls = []
        self.down = False
        self.routes = []
        self.add(r"/v1/acts/kodeks-na-truda$", fixture("act_meta.json"),
                 {"content-type": "application/json", "etag": '"m1"'})
        self.add(r"/v1/acts/kodeks-na-truda/text$", fixture("act_text_344.json"),
                 {"content-type": "application/json", "etag": '"t1"'})
        self.add(r"/v1/acts/kodeks-na-truda/history$", fixture("history_kt.json"),
                 {"content-type": "application/json"})
        self.add(r"/webapi/rdf/sparql\?", b'{"results": {"bindings": []}}',
                 {"content-type": "application/sparql-results+json"})
        self.add(r"/resource/celex/32016R0679$", fixture("eu_document.xhtml"),
                 {"content-type": "application/xhtml+xml;charset=UTF-8"})
        self.add(r"/resource/celex/32024R1689$", fixture("eu_aiact.xhtml"),
                 {"content-type": "application/xhtml+xml;charset=UTF-8"})
        self.add(r"/resource/celex/62018CJ0311$", fixture("eu_judgment.xhtml"),
                 {"content-type": "application/xhtml+xml;charset=UTF-8"})
        self.add(r"constcourt\.bg/bg/search-acts$", fixture("constcourt_index.html"),
                 {"content-type": "text/html; charset=utf-8"})
        self.add(r"constcourt\.bg/bg/act-\d+$", fixture("constcourt_act.html"),
                 {"content-type": "text/html; charset=utf-8"})
        # Lowest priority: a real search with no match answers an empty result set, not 404.
        self.routes.append((re.compile(r"/v1/acts\?"), 200,
                            b'{"total": 0, "offset": 0, "limit": 10, "results": []}',
                            {"content-type": "application/json"}))

    def add(self, pattern, body, headers=None, status=200):
        self.routes.insert(0, (re.compile(pattern), status, body, headers or {}))

    def __call__(self, url, headers):
        self.calls.append((url, dict(headers)))
        if self.down:
            raise OSError("network unreachable")
        for rx, status, body, hdrs in self.routes:
            if rx.search(url):
                if headers.get("If-None-Match") and headers.get("If-None-Match") == hdrs.get("etag"):
                    return client.Response(304, b"", {}, url)
                return client.Response(status, body, {k.lower(): v for k, v in hdrs.items()}, url)
        return client.Response(404, b'{"error": "not found"}', {}, url)


def make(settings=None, root=None):
    web = FakeWeb()
    http = client.Http(transport=web)
    client.MIN_INTERVAL_S = 0.0
    base = {"lsource_api_key": "sk_test"}
    base.update(settings or {})
    store = client.Store(http=http, cache=client.Cache(root or Path(tempfile.mkdtemp(dir=TMP))),
                         settings=lambda k, d=None: base.get(k, d))
    t = tools.Tools(store)
    return t, tools.make_handlers(t), web


def call(handlers, name, **args):
    return json.loads(handlers[name](args))


# ------------------------------------------------------------------ citation parsing

class CitationTests(unittest.TestCase):
    def test_bulgarian_forms(self):
        p = citations.parse_citation
        self.assertEqual(p("чл. 344 КТ"), {"kind": "article", "number": "344", "law": "КТ"})
        self.assertEqual(p("чл. 344, ал. 2 КТ"),
                         {"kind": "article", "number": "344", "paragraph": "2", "law": "КТ"})
        self.assertEqual(p("чл. 344, ал. 1, т. 3 КТ")["point"], "3")
        self.assertEqual(p("чл. 344, ал. 1, б. „в“ КТ")["letter"], "в")
        self.assertEqual(p("КТ чл. 344")["law"], "КТ")
        self.assertEqual(p("чл. 2а КТ")["number"], "2а")
        self.assertEqual(p("§ 1 ЗЗД")["kind"], "paragraph_section")
        self.assertEqual(p("чл. 5 от Конституцията")["law"], "Конституцията")
        self.assertEqual(p("чл. 6, ал. 1 от Закона за защита на личните данни")["law"],
                         "Закона за защита на личните данни")

    def test_latin_letter_normalised_to_cyrillic(self):
        p = citations.parse_citation
        self.assertEqual(p("чл. 2a КТ")["number"], "2а")
        self.assertEqual(p("чл. 3g КТ")["number"], "3г")
        # The letter itself is kept as typed; only Bulgarian rendering maps it.
        self.assertEqual(p("чл. 6, ал. 1, б. e ЗЗЛД")["letter"], "e")
        self.assertEqual(citations.cyrillic_letter("e"), "е")

    def test_eu_forms(self):
        p = citations.parse_citation
        g = p("чл. 6, пар. 1, б. „е“ ОРЗД")
        self.assertEqual((g["kind"], g["number"], g["paragraph"], g["letter"], g["law"]),
                         ("article", "6", "1", "е", "ОРЗД"))
        e = p("Art. 6(1)(f) GDPR")
        self.assertEqual((e["number"], e["paragraph"], e["letter"], e["law"]), ("6", "1", "f", "GDPR"))
        self.assertEqual(p("съображение 47 ОРЗД")["kind"], "recital")
        self.assertEqual(p("приложение III Регламент за ИИ"),
                         {"kind": "annex", "number": "III", "law": "Регламент за ИИ"})

    def test_split_section(self):
        self.assertEqual(citations.split_section("344")["number"], "344")
        self.assertEqual(citations.split_section("чл. 344, ал. 2")["paragraph"], "2")
        self.assertEqual(citations.split_section("2a")["number"], "2а")
        self.assertEqual(citations.split_section(None), {})


# ------------------------------------------------------------------ sources

class GazetteChainTests(unittest.TestCase):
    def test_two_layouts(self):
        kt = lsource.parse_gazette_chain(
            "Обн. ДВ. бр.26 от 1 Април 1986г., изм. ДВ. бр.6 от 22 Януари 1988г., "
            "изм. и доп. ДВ. бр.89 от 29 Септември 2026г.")
        self.assertEqual(len(kt), 3)
        self.assertEqual(kt[0]["issue"], "бр. 26/1986")
        self.assertEqual(kt[-1], {"action": "изм. и доп", "issue": "бр. 89/2026", "date": "2026-09-29"})
        kr = lsource.parse_gazette_chain(
            "Обн., ДВ, бр. 56 от 13 Юли 1991 г., в сила от 13 Юли 1991 г., изм. и доп., бр. 85 от "
            "26 Септември 2003 г., изм. и доп. бр. 106 от 22 Декември 2023 г.")
        self.assertEqual([row["issue"] for row in kr], ["бр. 56/1991", "бр. 85/2003", "бр. 106/2023"])
        self.assertEqual(kr[0]["date"], "1991-07-13")


class ActTextTests(unittest.TestCase):
    def test_structure(self):
        parsed = lsource.parse_act_text(payload("act_text_excerpt.json"))
        self.assertEqual(parsed["meta"]["title"], "Кодекс на труда")
        self.assertEqual(parsed["meta"]["in_force_from"], "01.01.1987")
        self.assertGreater(len(parsed["meta"]["gazette_chain"]), 20)
        self.assertEqual(parsed["chapters"][0]["heading"], "Глава първа.")
        self.assertEqual(parsed["articles"][0]["ref"], "Чл. 1")
        self.assertTrue(parsed["articles"][0]["scope"].startswith("Глава първа."))

    def test_heading_and_cyrillic_suffix(self):
        parsed = lsource.parse_act_text(payload("act_text_excerpt.json"))
        art = lsource.find_article(parsed, "2а")
        self.assertEqual(art["ref"], "Чл. 2а")
        self.assertEqual(art["heading"], "Двустранно сътрудничество")
        self.assertTrue(art["text"].startswith("Чл. 2а."))
        self.assertFalse(art["repealed"])

    def test_lookup_variants(self):
        parsed = lsource.parse_act_text(payload("act_text_excerpt.json"))
        for asked in ("2а", "2a", "2А"):
            self.assertIsNotNone(lsource.find_article(parsed, asked), asked)
        self.assertIsNone(lsource.find_article(parsed, "9999"))
        real = {a["number"] for a in parsed["articles"]}
        for ref in lsource.neighbours(parsed, "2"):
            self.assertIn(ref.replace("Чл. ", "").strip(), real)

    def test_article_scope_and_repealed(self):
        parsed = lsource.parse_act_text(payload("act_text_excerpt.json"))
        art = lsource.find_article(parsed, "12")
        self.assertTrue(art["scope"].startswith("Глава втора."))
        self.assertIn("Раздел I.", art["scope"])
        self.assertTrue(art["repealed"])

    def test_paragraph_split(self):
        text = ("Чл. 344. (Изм. - ДВ, бр. 100 от 1992 г.) (1) Първа алинея text.\n"
                "(2) Втора алинея text.\n(3) Трета алинея text.")
        parts = lsource.split_paragraphs(text)
        self.assertEqual([p["number"] for p in parts], ["1", "2", "3"])
        self.assertTrue(parts[0]["text"].startswith("(1) Първа"))
        self.assertTrue(parts[-1]["text"].endswith("Трета алинея text."))

    def test_paragraph_split_dotted(self):
        """Bulgarian editions of EU acts number paragraphs '1.', not '(1)'."""
        parts = lsource.split_paragraphs("1. Първи параграф text.\n2. Втори параграф text.")
        self.assertEqual([p["number"] for p in parts], ["1", "2"])

    def test_title_loose_match(self):
        self.assertEqual(lsource.loose_title("Кодекса на труда"), lsource.loose_title("Кодекс на труда"))
        self.assertEqual(lsource.loose_title("Закона за защита на личните данни"),
                         lsource.loose_title("Закон за защита на личните данни"))

    def test_alias_table_shape(self):
        self.assertEqual(lsource.ALIASES["кт"], "kodeks-na-truda")
        self.assertEqual(lsource.ABBR_BY_ID["kodeks-na-truda"], "КТ")
        self.assertEqual(len(lsource.ABBR_BY_ID), len(lsource._ABBR))


class EuMatchTests(unittest.TestCase):
    def test_aliases_and_numbers(self):
        self.assertEqual(eu.match_eu("ОРЗД")["celex"], "32016R0679")
        self.assertEqual(eu.match_eu("GDPR")["celex"], "32016R0679")
        self.assertEqual(eu.match_eu("Регламент за ИИ")["celex"], "32024R1689")
        self.assertEqual(eu.match_eu("AI Act")["celex"], "32024R1689")
        self.assertEqual(eu.match_eu("Регламент (ЕС) 2016/679")["celex"], "32016R0679")
        self.assertEqual(eu.match_eu("Директива (ЕС) 2022/2555")["celex"], "32022L2555")
        self.assertEqual(eu.match_eu("32023R2854")["celex"], "32023R2854")
        self.assertIsNone(eu.match_eu("КТ"))
        self.assertIsNone(eu.match_eu("ЗЗД"))

    def test_unit_ids(self):
        self.assertEqual(eu.unit_id("article", "6"), "art_6")
        self.assertEqual(eu.unit_id("recital", "47"), "rct_47")
        self.assertEqual(eu.unit_id("annex", "III"), "anx_III")
        self.assertEqual(eu.unit_id("annex", "3"), "anx_III")

    def test_cases(self):
        self.assertEqual(eu.match_case("Schrems II")["celex"], "62018CJ0311")
        self.assertEqual(eu.match_case("C-311/18")["celex"], "62018CJ0311")
        self.assertEqual(eu.match_case("62018CJ0311")["celex"], "62018CJ0311")
        self.assertIsNone(eu.match_case("нещо си"))


class EuDocumentTests(unittest.TestCase):
    def test_article_recital(self):
        doc = eu._parse_doc(fixture("eu_document.xhtml"), client.Response(
            200, b"", {"content-type": "application/xhtml+xml;charset=UTF-8"},
            "https://publications.europa.eu/resource/celex/32016R0679"))
        self.assertIn("art_6", doc["units"])
        self.assertIn("rct_47", doc["units"])
        self.assertIn("Законосъобразност", doc["units"]["art_6"]["text"])
        self.assertIn("Законните интереси", doc["units"]["rct_47"]["text"])

    def test_annex(self):
        doc = eu._parse_doc(fixture("eu_aiact.xhtml"), client.Response(
            200, b"", {"content-type": "application/xhtml+xml;charset=UTF-8"},
            "https://publications.europa.eu/resource/celex/32024R1689"))
        self.assertIn("art_50", doc["units"])
        self.assertIn("anx_III", doc["units"])
        self.assertIn("ПРИЛОЖЕНИЕ III", doc["units"]["anx_III"]["text"])
        self.assertIn("Задължения за прозрачност", doc["units"]["art_50"]["text"])

    def test_judgment_points(self):
        doc = eu._parse_judgment(fixture("eu_judgment.xhtml"), client.Response(
            200, b"", {"content-type": "application/xhtml+xml;charset=UTF-8"},
            "https://publications.europa.eu/resource/celex/62018CJ0311"))
        self.assertIn("201", doc["points"])
        self.assertIn("ЩЛД е невалидно", doc["points"]["201"])
        self.assertFalse(doc["points"]["201"].startswith("|"))
        self.assertFalse(doc["points"]["201"].startswith("201"))


class ConstitutionalCourtTests(unittest.TestCase):
    def test_index(self):
        rows = constcourt._parse_index(fixture("constcourt_index.html"), client.Response(
            200, b"", {}, constcourt.LIST_URL))
        self.assertEqual(len(rows), 2)
        first = rows[0]
        self.assertEqual(first["doc_id"], "act-10457")
        self.assertEqual(first["date"], "2026-10-20")
        self.assertEqual((first["case_number"], first["case_year"]), ("15", "2026"))
        self.assertEqual(first["act_type"], "становище по решение")
        self.assertTrue(first["judges"])

    def test_act(self):
        doc = constcourt._parse_act(fixture("constcourt_act.html"), client.Response(
            200, b"", {}, "https://www.constcourt.bg/bg/act-10457"))
        self.assertEqual(doc["act_type"], "становище по решение")
        self.assertEqual(doc["date"], "2026-10-20")
        self.assertEqual(doc["case_number"], "15/2026")
        self.assertNotIn('id="document-content"', doc["text"])
        self.assertIn("Становище", doc["text"])


# ------------------------------------------------------------------ tools

class BgLawToolTests(unittest.TestCase):
    def test_get_with_source_and_version(self):
        _, h, web = make()
        r = call(h, "bg_law_get", citation="чл. 344, ал. 1 КТ")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["provision"]["citation"], "чл. 344, ал. 1 КТ")
        self.assertEqual(r["law"]["title"], "Кодекс на труда")
        self.assertEqual(r["law"]["abbreviation"], "КТ")
        self.assertEqual(r["version"]["issue"], "ДВ бр. 89/2026")
        self.assertGreater(r["version"]["gazette_count"], 20)
        self.assertIn("обвързва обнародваната в Държавен вестник редакция", r["source"]["attribution"])
        self.assertEqual(r["source"]["cache"], "fresh")
        # The article number never leaves the machine: the whole act is fetched once.
        self.assertFalse(any("articles/344" in url for url, _ in web.calls))

    def test_paragraph_pinpoint(self):
        _, h, _ = make()
        r = call(h, "bg_law_get", citation="чл. 344, ал. 2 КТ")
        self.assertTrue(r["ok"], r)
        self.assertTrue(r["provision"]["text"].startswith("(2)"), r["provision"]["text"][:40])
        r = call(h, "bg_law_get", citation="чл. 344, ал. 9 КТ")
        self.assertEqual(r["error_type"], "paragraph_not_found")
        self.assertEqual(r["paragraphs"], ["1", "2", "3", "4"])

    def test_missing_article_never_guesses(self):
        _, h, _ = make()
        r = call(h, "bg_law_get", citation="чл. 9999 КТ")
        self.assertEqual(r["error_type"], "article_not_found")
        self.assertIn("Do not guess", r["next_step"])

    def test_as_of_uses_the_api_article(self):
        _, h, web = make()
        web.add(r"articles/344\?as_of=2026-01-01$", json.dumps(
            {"id": "kodeks-na-truda", "law": "Кодекс на труда", "number": "344", "ref": "чл. 344",
             "scope": "", "text": "Чл. 344. (1) Стара редакция."}).encode(),
            {"content-type": "application/json"})
        r = call(h, "bg_law_get", citation="чл. 344, ал. 1 КТ", as_of="2026-01-01")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["as_of"], "2026-01-01")
        self.assertIn("Стара редакция", r["provision"]["text"])
        self.assertEqual(call(h, "bg_law_get", citation="чл. 344 КТ", as_of="01.01.2026")["error_type"],
                         "bad_argument")

    def test_key_required_without_a_key(self):
        _, h, _ = make(settings={"lsource_api_key": ""})
        # The plugin falls back to LSOURCE_API_KEY, so the variable must be blank for this case.
        with mock.patch.dict(os.environ, {"LSOURCE_API_KEY": ""}, clear=False):
            r = call(h, "bg_law_get", citation="чл. 344 КТ")
        self.assertEqual(r["error_type"], "key_required")
        self.assertIn("lsource.net/developers", r["next_step"])

    def test_truncation(self):
        _, h, _ = make()
        r = call(h, "bg_law_get", citation="чл. 344 КТ", max_chars=500)
        self.assertTrue(r["provision"]["truncated"])
        self.assertLessEqual(len(r["provision"]["text"]), 500)

    def test_cache_states(self):
        root = Path(tempfile.mkdtemp(dir=TMP))
        _, h, web = make(root=root)
        self.assertEqual(call(h, "bg_law_get", citation="чл. 344 КТ")["source"]["cache"], "fresh")
        n = len(web.calls)
        self.assertEqual(call(h, "bg_law_get", citation="чл. 341 КТ")["source"]["cache"], "cached")
        self.assertEqual(len(web.calls), n)
        _, h2, web2 = make(settings={"max_age_hours": 0}, root=root)
        self.assertEqual(call(h2, "bg_law_get", citation="чл. 344 КТ")["source"]["cache"], "revalidated")
        self.assertTrue(any(hd.get("If-None-Match") == '"t1"' for _, hd in web2.calls))
        web2.down = True
        r = call(h2, "bg_law_get", citation="чл. 344 КТ")
        self.assertTrue(r["stale"])
        self.assertEqual(r["source"]["cache"], "stale")

    def test_offline_without_cache(self):
        _, h, web = make()
        web.down = True
        self.assertEqual(call(h, "bg_law_get", citation="чл. 344 КТ")["error_type"], "network")

    def test_search(self):
        _, h, web = make()
        web.add(r"/v1/acts\?", fixture("acts_search.json"), {"content-type": "application/json"})
        r = call(h, "bg_law_search", query="кодекс")
        self.assertEqual(r["results"][0]["jurisdiction"], "BG")
        self.assertEqual(r["results"][0]["law"], "kodeks-na-truda")
        r = call(h, "bg_law_search", query="GDPR")
        self.assertEqual(r["results"][0]["jurisdiction"], "EU")

    def test_toc(self):
        _, h, _ = make()
        r = call(h, "bg_law_toc", law="КТ", limit=5)
        self.assertEqual(r["entries"][0]["id"], "Чл. 325")
        self.assertIn("Глава шестнадесета.", r["version"]["chapters"][0])
        r = call(h, "bg_law_toc", law="КТ", query="оспорва законността на уволнението")
        self.assertTrue(r["entries"], r)
        self.assertEqual(r["entries"][0]["id"], "Чл. 344")
        self.assertEqual(r["entries"][0]["match"], "text")
        self.assertIn("оспорва законността", r["entries"][0]["snippet"])
        r = call(h, "bg_law_toc", law="КТ", query="незаконно уволнение")
        self.assertEqual(r["entries"][0]["match"], "heading")

    def test_history(self):
        _, h, _ = make()
        r = call(h, "bg_law_history", law="КТ", limit=2)
        self.assertTrue(r["ok"], r)
        expected = lsource.parse_act_text(payload("act_text_344.json"))["meta"]["gazette_chain"]
        self.assertEqual(r["gazette_chain"]["total"], len(expected))
        self.assertEqual(len(r["gazette_chain"]["entries"]), 2)
        self.assertEqual(r["point_in_time"]["versions"][0]["issue"], "ДВ бр. 89/2026")
        self.assertIn("Point-in-time text exists from", r["point_in_time"]["limitation"])

    def test_language_setting(self):
        _, h, _ = make(settings={"language": "en"})
        r = call(h, "bg_law_get", citation="чл. 344 КТ")
        self.assertTrue(r["source"]["attribution"].startswith("Source: "), r["source"]["attribution"][:60])


class EuToolTests(unittest.TestCase):
    def test_article(self):
        _, h, web = make()
        r = call(h, "bg_law_get", citation="чл. 6, пар. 1, б. „е“ ОРЗД", max_chars=3000)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["jurisdiction"], "EU")
        self.assertEqual(r["provision"]["id"], "art_6")
        self.assertEqual(r["provision"]["title"], "Законосъобразност на обработването")
        self.assertTrue(r["provision"]["text"].startswith("1. Обработването"), r["provision"]["text"][:60])
        self.assertEqual(r["provision"]["citation"], "чл. 6, пар. 1, б. „е“ от ОРЗД")
        self.assertIn("CC BY 4.0", r["source"]["attribution"])
        self.assertTrue(all("eur-lex.europa.eu" not in url for url, _ in web.calls))

    def test_recital_and_annex(self):
        _, h, _ = make()
        r = call(h, "bg_law_get", citation="съображение 47 ОРЗД", max_chars=2000)
        self.assertEqual(r["provision"]["id"], "rct_47")
        self.assertIn("Законните интереси", r["provision"]["text"])
        r = call(h, "bg_law_get", citation="приложение 3 Регламент за ИИ", max_chars=2000)
        self.assertEqual(r["provision"]["id"], "anx_III")

    def test_missing_paragraph_and_switch(self):
        _, h, _ = make()
        r = call(h, "bg_law_get", citation="чл. 6, пар. 9 ОРЗД")
        self.assertEqual(r["error_type"], "paragraph_not_found")
        _, h2, _ = make(settings={"eu_law": False})
        self.assertEqual(call(h2, "bg_law_get", citation="чл. 6 ОРЗД")["error_type"], "disabled")

    def test_toc(self):
        _, h, _ = make()
        r = call(h, "bg_law_toc", law="ОРЗД", limit=5)
        ids = [e["id"] for e in r["entries"]]
        self.assertIn("чл. 6", ids)
        self.assertIn("съображение 47", ids)


class CaseToolTests(unittest.TestCase):
    def test_search_by_case_number(self):
        _, h, _ = make()
        r = call(h, "bg_case_search", case_number="15/2026")
        self.assertTrue(r["ok"], r)
        self.assertEqual([x["doc_id"] for x in r["results"]], ["act-10457", "act-10459"])
        self.assertEqual(call(h, "bg_case_search")["error_type"], "bad_argument")
        self.assertEqual(call(h, "bg_case_search", date_from="gestern")["error_type"], "bad_argument")
        self.assertTrue(call(h, "bg_case_search", act_type="решение", date_from="2026-10-01")["ok"])

    def test_get_ambiguous_then_read(self):
        _, h, _ = make()
        r = call(h, "bg_case_get", case_number="15/2026")
        self.assertEqual(r["error_type"], "ambiguous_case")
        self.assertEqual(len(r["candidates"]), 2)
        r = call(h, "bg_case_get", doc_id="act-10457", max_chars=2000)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["decision"]["date"], "2026-10-20")
        self.assertEqual(r["decision"]["case_number"], "15/2026")
        self.assertIn("Становище", r["parts"]["text"])
        self.assertIn("Конституционен съд", r["source"]["attribution"])

    def test_cjeu_judgment(self):
        _, h, _ = make()
        r = call(h, "bg_case_get", celex="Schrems II", paragraph="201", max_chars=2000)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["decision"]["celex"], "62018CJ0311")
        self.assertEqual(r["decision"]["paragraphs"], 2)  # the fixture carries two points
        self.assertIn("ЩЛД е невалидно", r["parts"]["text"])
        self.assertEqual(call(h, "bg_case_get", celex="62018CJ0311", paragraph="999")["error_type"],
                         "paragraph_not_found")

    def test_switch(self):
        _, h, _ = make(settings={"case_law": False})
        self.assertEqual(call(h, "bg_case_search", case_number="15/2026")["error_type"], "disabled")


class SafetyTests(unittest.TestCase):
    def test_hosts(self):
        http = client.Http(transport=lambda url, hd: client.Response(200, b"x", {}, url))
        with self.assertRaises(client.LawError):
            http.get("https://example.com/x")
        with self.assertRaises(client.LawError):
            http.get("ftp://lsource.net/x")
        self.assertTrue(http.get("http://lsource.net/x").url.startswith("https://"))

    def test_redirect_to_foreign_host_refused(self):
        def transport(url, hd):
            return client.Response(302, b"", {"location": "https://evil.example/x"}, url)

        with self.assertRaises(client.LawError) as ctx:
            client.Http(transport=transport).get("https://lsource.net/v1/acts")
        self.assertEqual(ctx.exception.kind, "network")

    def test_size_cap(self):
        http = client.Http(transport=lambda url, hd: client.Response(200, b"x" * 2000, {}, url))
        with self.assertRaises(client.LawError) as ctx:
            http.get("https://lsource.net/x", max_bytes=1000)
        self.assertEqual(ctx.exception.kind, "too_large")

    def test_status_errors(self):
        def err(status):
            return client._status_error(client.Response(status, b"{}", {}, "https://lsource.net/v1/x")).kind

        self.assertEqual(err(409), "withheld")
        self.assertEqual(err(401), "unauthorized")
        self.assertEqual(err(429), "rate_limited")
        self.assertEqual(err(404), "not_found")
        self.assertEqual(err(503), "upstream")

    def test_handlers_never_raise(self):
        t, h, _ = make()
        for name in h:
            self.assertIn("ok", json.loads(h[name](None)))
            self.assertIn("ok", json.loads(h[name]({"citation": 5, "law": ["x"], "max_chars": "lots"})))
        t.law_get = lambda args: 1 / 0
        boom = tools.make_handlers(t)["bg_law_get"]({})
        self.assertEqual(json.loads(boom)["error_type"], "internal")

    def test_prune_and_cache_keys(self):
        cache = client.Cache(Path(tempfile.mkdtemp(dir=TMP)))
        for i in range(5):
            cache.write(f"k{i}", "x" * 10000, {"used_at": i})
        self.assertGreaterEqual(cache.prune(25000, keep=("k0",)), 2)
        self.assertIsNotNone(cache.read("k0")[0])
        self.assertLessEqual(cache.size_bytes(), 25000)
        cache.write("../../evil key", {"a": 1}, {})
        self.assertTrue(all(p.parent == cache.root for p in cache.root.iterdir()))


class ManifestTests(unittest.TestCase):
    def test_registration_matches_manifest(self):
        manifest = (ROOT / "plugin.yaml").read_text(encoding="utf-8")
        declared = re.findall(r"^\s+- (bg_\w+)$", manifest, re.M)

        class Ctx:
            def __init__(self):
                self.tools, self.skills = [], []

            def register_tool(self, name, toolset, schema, handler, **kw):
                self.tools.append((name, toolset, schema, handler))

            def register_skill(self, name, path, description=""):
                self.skills.append((name, Path(path).exists(), description))

            def get_config(self, key, default=None):
                return default

        ctx = Ctx()
        pkg.register(ctx)
        self.assertEqual(sorted(t[0] for t in ctx.tools), sorted(declared))
        self.assertEqual({t[1] for t in ctx.tools}, {"bg_law"})
        self.assertEqual(ctx.skills[0][:2], ("read-law", True))
        self.assertIn("version: " + __import__("bg_law_plugin.version", fromlist=["x"]).__version__, manifest)

    def test_listing_sentences(self):
        """Tool Search shows the first sentence (about 60 chars): it must name the domain."""
        allowed = ("Bulgarian and EU law", "Bulgarian law", "Bulgarian courts", "Bulgarian and EU courts")
        for schema in schemas.ALL_SCHEMAS:
            first = re.split(r"(?<=[.!?])\s", schema["description"], maxsplit=1)[0]
            self.assertLessEqual(len(first), 60, first)
            self.assertTrue(first.startswith(allowed), first)

    def test_no_dashes_in_texts(self):
        bad = []
        for path in ROOT.rglob("*"):
            if (path.is_file() and path.suffix in (".py", ".md", ".yaml", ".json", ".xhtml")
                    and ".git" not in path.parts and "fixtures" not in path.parts):
                text = path.read_text(encoding="utf-8")
                for i, line in enumerate(text.splitlines(), 1):
                    if "\u2014" in line or "\u2013" in line:
                        bad.append(f"{path.name}:{i}")
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
