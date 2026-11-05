"""Regression checks for text identity, version selection, bounded work and settings."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from test_offline import make, call, fixture
from bg_law_plugin import client, eu, lsource, settings, tools


class AuditTests(unittest.TestCase):
    def test_tele2_canonical_judgment(self):
        self.assertEqual(eu.match_case("C-203/15")["celex"], "62015CJ0203")
        self.assertEqual(eu.match_case("C-698/15")["celex"], "62015CJ0203")

    def test_cache_keys_and_interleaved_writes(self):
        cache = client.Cache(Path(tempfile.mkdtemp()))
        for key in ("p3-search-чл. 5", "p3-search-чл. 6", "law.foo", "law.bar"):
            cache.write(key, key, {})
        for key in ("p3-search-чл. 5", "p3-search-чл. 6", "law.foo", "law.bar"):
            self.assertEqual(cache.read(key)[0], key)
        data, meta = cache._paths("law.foo")
        data.write_text('"corrupt pair"', encoding="utf-8")
        self.assertEqual(cache.read("law.foo"), (None, None))

    def test_redirect_credentials_removed(self):
        seen = []
        def transport(url, headers):
            seen.append(dict(headers))
            return client.Response(302, b"", {"location": "https://www.constcourt.bg/bg/search-acts"}, url) if len(seen) == 1 else client.Response(200, b"ok", {}, url)
        client.Http(transport).get("https://lsource.net/v1/", {"Authorization": "Bearer secret", "X-Api-Key": "secret", "Cookie": "secret"})
        self.assertFalse(any(k.lower() in ("authorization", "x-api-key", "cookie") for k in seen[1]))

    def test_invalid_pinpoints_are_errors(self):
        _, h, _ = make()
        for citation, error in (("чл. 344, ал. 1, т. 9999 КТ", "point_not_found"),
                                ("Art. 6(1)(z) GDPR", "letter_not_found")):
            self.assertEqual(call(h, "bg_law_get", citation=citation)["error_type"], error)

    def test_valid_point_is_extracted(self):
        _, h, _ = make()
        r = call(h, "bg_law_get", citation="чл. 344, ал. 1, т. 3 КТ")
        self.assertTrue(r["ok"], r)
        self.assertTrue(r["pinpoint_verified"])
        self.assertTrue(r["provision"]["text"].startswith("3."))
        self.assertNotIn("4.", r["provision"]["text"])

    def test_inserted_paragraph_and_duplicate_sections(self):
        parts = lsource.split_paragraphs("(1) One.\n(2) Two.\n(2а) Inserted.\n(3) Three.")
        self.assertEqual([p["number"] for p in parts], ["1", "2", "2а", "3"])
        doc = lsource.parse_act_text({"text": "Чл. 1. Main\n§ 1. First\n§ 1. Second"})
        with self.assertRaises(client.LawError) as err:
            lsource.find_article(doc, "1", "paragraph_section")
        self.assertEqual(err.exception.kind, "ambiguous_provision")

    def test_historical_provenance_and_missing_current_article(self):
        _, h, web = make()
        body = {"number": "999", "text": "Чл. 999. Historical only.", "at_date": "2025-12-01", "issue": "ДВ бр. 1/2025"}
        web.add(r"/articles/999\?as_of=2026-01-01$", json.dumps(body).encode())
        r = call(h, "bg_law_get", citation="чл. 999 КТ", as_of="2026-01-01")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["as_of_used"], "2025-12-01")
        self.assertEqual(r["version"]["issue"], "ДВ бр. 1/2025")
        self.assertNotIn("89/2026", r["source"]["attribution"])

    def test_empty_and_newer_historical_text_refused(self):
        for body in ({"text": "", "at_date": "2025-12-01"},
                     {"text": "TODAY", "at_date": "2026-10-01"}):
            _, h, web = make()
            web.add(r"/articles/344\?as_of=2026-01-01$", json.dumps(body).encode())
            self.assertFalse(call(h, "bg_law_get", citation="чл. 344 КТ", as_of="2026-01-01")["ok"])

    def test_historical_section_uses_whole_dated_document(self):
        _, h, web = make()
        body = {"text": "Чл. 1. Wrong family\n§ 1. Correct historical section.", "at_date": "2025-12-01", "issue": "ДВ бр. 1/2025"}
        web.add(r"/text\?as_of=2026-01-01$", json.dumps(body).encode())
        r = call(h, "bg_law_get", citation="§ 1 КТ", as_of="2026-01-01")
        self.assertTrue(r["ok"], r)
        self.assertTrue(r["provision"]["text"].startswith("§ 1."))
        self.assertFalse(any("/articles/1" in url for url, _ in web.calls))

    def test_eu_history_without_bg_key_and_explicit_revision(self):
        _, h, web = make({"lsource_api_key": ""})
        with mock.patch.dict("os.environ", {"LSOURCE_API_KEY": ""}):
            self.assertTrue(call(h, "bg_law_history", law="GDPR")["ok"])
        revision = "02016R0679-20160504"
        web.add(r"/resource/celex/" + revision + "$", fixture("eu_document.xhtml"), {"content-type": "application/xhtml+xml"})
        r = call(h, "bg_law_get", citation="Art. 6 GDPR", revision=revision)
        self.assertEqual(r["version"]["document"], revision)
        r = call(h, "bg_law_get", citation="Art. 6 GDPR", revision="02016R0679-20000101")
        self.assertEqual(r["error_type"], "as_of_not_available")
        self.assertEqual(eu.match_eu(revision)["document"], revision)

    def test_case_scan_counts_failures(self):
        t, _, _ = make()
        t.cases.rows = lambda: [{"doc_id": str(i), "date": "2026-01-01", "case_number": "", "case_year": "", "act_type": "решение"} for i in range(5)]
        with mock.patch.object(t.cases, "act", side_effect=client.LawError("network", "offline")) as request:
            rows, attempts, capped = t.cases.search(query="term", scan_limit=1)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(attempts, 1)
        self.assertTrue(capped)

    def test_stale_court_and_strict_freshness(self):
        for allow in (True, False):
            t, h, web = make({"max_age_hours": 0, "allow_stale": allow})
            call(h, "bg_case_get", doc_id="act-10457")
            web.down = True
            r = call(h, "bg_case_get", doc_id="act-10457")
            self.assertEqual(r["ok"], allow)
            if allow:
                self.assertTrue(r["stale"])
                self.assertEqual(r["source"]["cache"], "stale")

    def test_full_text_continuation(self):
        _, h, _ = make()
        complete = call(h, "bg_law_get", citation="чл. 344 КТ", max_chars=30000)["provision"]["text"]
        pieces, offset = [], 0
        while True:
            p = call(h, "bg_law_get", citation="чл. 344 КТ", max_chars=500, offset=offset)["provision"]
            pieces.append(p["text"])
            if not p["truncated"]:
                break
            offset = p["next_offset"]
        self.assertEqual("".join(pieces), complete)

    def test_search_filter_total_and_pagination(self):
        _, h, web = make()
        self.assertEqual(call(h, "bg_law_search", query="КТ", kind="наредба")["results"], [])
        web.add(r"/acts\?q=tax&limit=1&offset=1$", b'{"total":4,"results":[{"id":"second-law","title":"Second"}]}')
        r = call(h, "bg_law_search", query="tax", limit=1, offset=1)
        self.assertEqual((r["total_matches"], r["next_offset"]), (4, 2))

    def test_new_api_tools_and_export(self):
        _, h, web = make()
        web.add(r"/changes\?", b'{"diff":"changed","from_date":"2026-01-01","to_date":"2026-10-01"}')
        web.add(r"/transposition", b'{"total":1,"items":[{"quote":"source sentence","eu":{"id":null}}]}')
        web.add(r"/acts/recent$", b'{"recent":[{"id":"one"},{"id":"two"}]}')
        web.add(r"/akn$", b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0"><act/></akomaNtoso>')
        for name, args in (("bg_law_changes", {"law":"КТ","date_from":"2026-01-01","date_to":"2026-10-01"}),
                           ("bg_law_transposition", {"query":"directive"}), ("bg_law_recent", {}),
                           ("bg_law_export", {"law":"КТ"})):
            self.assertTrue(call(h, name, **args)["ok"], name)

    def test_settings_validation_and_redaction(self):
        for data in ({"max_age_hours": -1}, {"cache_max_mb": 0}, {"allow_stale": "false"}, {"eu_language": "xx"}):
            with self.assertRaises(client.LawError):
                settings.validate(data)
        t, _, _ = make()
        with mock.patch.object(settings, "diagnostics_path", return_value=Path(tempfile.mkdtemp()) / "diagnostics.json"):
            report = settings.status(t.store)
        self.assertNotIn("sk_test", json.dumps(report))
        self.assertTrue(report["key_stored"])

    def test_calendar_dates_and_switches(self):
        _, h, _ = make({"bg_law": False, "eu_cases": False})
        self.assertEqual(call(h, "bg_law_toc", law="КТ")["error_type"], "disabled")
        self.assertEqual(call(h, "bg_case_get", celex="62018CJ0311")["error_type"], "disabled")
        with self.assertRaises(client.LawError):
            tools._date("2026-02-30")

    def test_historical_lowercase_article_ref_stays_an_article(self):
        _, h, web = make()
        body = {"number": "344", "ref": "чл. 344", "text": "Чл. 344. Old text.", "at_date": "2025-12-01"}
        web.add(r"/articles/344\?as_of=2026-01-01$", json.dumps(body).encode())
        r = call(h, "bg_law_get", citation="чл. 344 КТ", as_of="2026-01-01")
        self.assertEqual(r["provision"]["citation"], "чл. 344 КТ")

    def test_akn_point_extraction(self):
        _, h, web = make()
        xml = b'<akomaNtoso><act><article eId="art_344"><paragraph><num>(1)</num><point><num>3.</num><content><p>STRUCTURED POINT</p></content></point></paragraph></article></act></akomaNtoso>'
        web.add(r"/akn$", xml)
        r = call(h, "bg_law_get", citation="чл. 344, ал. 1, т. 3 КТ")
        self.assertTrue(r["ok"], r)
        self.assertIn("STRUCTURED POINT", r["provision"]["text"])

    def test_stale_version_index_is_reported(self):
        _, h, web = make({"max_age_hours": 0})
        self.assertTrue(call(h, "bg_law_get", citation="Art. 6 GDPR")["ok"])
        web.add(r"/webapi/rdf/sparql\?", b"unavailable", status=503)
        r = call(h, "bg_law_get", citation="Art. 6 GDPR")
        self.assertTrue(r["ok"], r)
        self.assertTrue(r["stale"])

    def test_status_does_not_probe_unless_requested(self):
        t, h, web = make()
        with mock.patch.object(settings, "diagnostics_path", return_value=Path(tempfile.mkdtemp()) / "diagnostics.json"):
            r = call(h, "bg_law_status")
        self.assertTrue(r["ok"])
        self.assertEqual(web.calls, [])


if __name__ == "__main__":
    unittest.main()
