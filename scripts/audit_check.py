"""Live patch regressions. Set LSOURCE_API_KEY; no private documents are submitted."""

import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("bg_patch_live", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
pkg = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = pkg
spec.loader.exec_module(pkg)
from bg_patch_live import client, tools


def main():
    if not os.environ.get("LSOURCE_API_KEY"):
        raise SystemExit("Set LSOURCE_API_KEY before running the live patch checks.")
    failures = []
    with tempfile.TemporaryDirectory(prefix="bg-law-patch-") as directory:
        store = client.Store(cache=client.Cache(Path(directory)),
                             settings=lambda k, d=None: os.environ["LSOURCE_API_KEY"] if k == "lsource_api_key" else d)
        handlers = tools.make_handlers(tools.Tools(store))
        cases = [
            ("Tele2", "bg_case_get", {"case_number": "C-203/15", "language": "en"}, lambda r: r.get("decision", {}).get("celex") == "62015CJ0203" and "Tele2" in r.get("parts", {}).get("text", "")),
            ("general case number", "bg_case_get", {"case_number": "C-621/18", "language": "en", "max_chars": 500}, lambda r: r.get("ok")),
            ("historical section", "bg_law_get", {"citation": "§ 1 ЗЗД", "as_of": "2026-08-15"}, lambda r: r.get("provision", {}).get("text", "").startswith("§ 1.")),
            ("invalid point", "bg_law_get", {"citation": "чл. 344, ал. 1, т. 9999 КТ"}, lambda r: r.get("error_type") == "point_not_found"),
            ("structured point", "bg_law_get", {"citation": "чл. 344, ал. 1, т. 3 КТ"}, lambda r: r.get("pinpoint_verified") and "обезщетение" in r.get("provision", {}).get("text", "")),
            ("English letter", "bg_law_get", {"citation": "Art. 6(1)(f) GDPR", "language": "en"}, lambda r: "legitimate interests" in r.get("provision", {}).get("text", "")),
            ("invalid letter", "bg_law_get", {"citation": "Art. 6(1)(z) GDPR", "language": "en"}, lambda r: r.get("error_type") == "letter_not_found"),
            ("EU snapshot", "bg_law_get", {"citation": "Art. 6 GDPR", "revision": "02016R0679-20160504"}, lambda r: r.get("version", {}).get("document") == "02016R0679-20160504"),
            ("changes", "bg_law_changes", {"law": "КТ", "date_from": "2026-07-01", "date_to": "2026-10-01"}, lambda r: r.get("ok") and "from_date" in r.get("data", {})),
            ("recent amendments", "bg_law_recent", {"limit": 2}, lambda r: len(r.get("data", {}).get("recent", [])) == 2),
            ("transposition", "bg_law_transposition", {"law": "КТ"}, lambda r: bool(r.get("data", {}).get("transposes"))),
            ("XML export", "bg_law_export", {"law": "КТ", "format": "akn", "max_chars": 500}, lambda r: r.get("document", {}).get("truncated") and "akomaNtoso" in r.get("document", {}).get("text", "")),
            ("EU title search", "bg_law_search", {"query": "protection of natural persons", "jurisdiction": "EU", "limit": 2}, lambda r: bool(r.get("results"))),
            ("EU court search", "bg_case_search", {"query": "Tele2", "court": "EU", "limit": 2}, lambda r: bool(r.get("results"))),
        ]
        for label, name, args, predicate in cases:
            result = json.loads(handlers[name](args))
            passed = bool(predicate(result))
            print(("PASS" if passed else "FAIL") + " " + label, flush=True)
            if not passed:
                failures.append(label)
                print(json.dumps(result, ensure_ascii=False)[:1000], flush=True)
        print(f"{len(cases) - len(failures)}/{len(cases)} patch checks passed")
    return bool(failures)


if __name__ == "__main__":
    raise SystemExit(main())
