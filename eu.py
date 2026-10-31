"""EU law from the Publications Office of the European Union (CELLAR).

Text: content negotiation on https://publications.europa.eu/resource/celex/<CELEX>
(Accept: application/xhtml+xml, Accept-Language: bul|eng). Consolidated versions are
listed with one SPARQL query on the Publications Office endpoint. EUR-Lex itself is
not contacted (it answers automated clients with HTTP 202).

The Bulgarian and English editions carry the same structure ids as every other
language edition: eli-subdivision ids art_7, rct_47, anx_III for legislation and
point12 for a judgment of the Court of Justice.
"""

from __future__ import annotations

import json
import re
import time
import urllib.parse
from typing import Any, Dict, List, Optional, Tuple

from .citations import int_to_roman, roman_to_int
from .client import LawError, Response, Store
from .lsource import fold
from .textutil import html_to_text, parse_html
from .version import PARSE_VERSION

CELLAR = "https://publications.europa.eu/resource/celex/"
SPARQL = "https://publications.europa.eu/webapi/rdf/sparql"
LANG_CODES = {"bg": "bul", "en": "eng"}

# Well-known acts, each CELEX checked against CELLAR in Bulgarian (title and
# ELI-structured XHTML). Older acts without ELI markup (e.g. 2002/58/EC) are not listed.
KNOWN_ACTS: List[Dict[str, Any]] = [
    {"celex": "32016R0679", "bg": "ОРЗД", "en": "GDPR",
     "aliases": ["Регламент (ЕС) 2016/679", "Общ регламент относно защитата на данните",
                 "GDPR", "DSGVO", "General Data Protection Regulation", "Регламент 2016/679"]},
    {"celex": "32024R1689", "bg": "Регламент за ИИ", "en": "AI Act",
     "aliases": ["Закон за изкуствения интелект", "Регламент (ЕС) 2024/1689", "AI Act", "AIA",
                 "EU AI Act", "KI-VO", "Регламент за изкуствен интелект"]},
    {"celex": "32022R2065", "bg": "Закон за цифровите услуги", "en": "DSA",
     "aliases": ["DSA", "Digital Services Act", "Регламент (ЕС) 2022/2065", "ЗЦУ"]},
    {"celex": "32022R1925", "bg": "Закон за цифровите пазари", "en": "DMA",
     "aliases": ["DMA", "Digital Markets Act", "Регламент (ЕС) 2022/1925", "ЗЦП"]},
    {"celex": "32022L2555", "bg": "Директива за мрежовата и информационна сигурност", "en": "NIS2",
     "aliases": ["NIS2", "NIS 2", "NIS-2", "Директива (ЕС) 2022/2555", "НИС2"]},
    {"celex": "32022R2554", "bg": "Регламент за цифровата оперативна устойчивост", "en": "DORA",
     "aliases": ["DORA", "Digital Operational Resilience Act", "Регламент (ЕС) 2022/2554"]},
    {"celex": "32023R2854", "bg": "Регламент за данните", "en": "Data Act",
     "aliases": ["Data Act", "EU Data Act", "Регламент (ЕС) 2023/2854", "Закон за данните"]},
    {"celex": "32022R0868", "bg": "Регламент за управление на данните", "en": "DGA",
     "aliases": ["DGA", "Data Governance Act", "Регламент (ЕС) 2022/868"]},
    {"celex": "32014R0910", "bg": "Регламент eIDAS", "en": "eIDAS",
     "aliases": ["eIDAS", "Регламент (ЕС) 910/2014", "Регламент (ЕС) № 910/2014"]},
    {"celex": "32023R1114", "bg": "Регламент MiCA", "en": "MiCA",
     "aliases": ["MiCA", "MiCAR", "Markets in Crypto-Assets", "Регламент (ЕС) 2023/1114"]},
    {"celex": "32024R2847", "bg": "Регламент за киберустойчивост", "en": "CRA",
     "aliases": ["CRA", "Cyber Resilience Act", "Регламент (ЕС) 2024/2847"]},
    {"celex": "32019L1937", "bg": "Директива за лицата, подаващи сигнали", "en": "Whistleblower Directive",
     "aliases": ["Whistleblower Directive", "Директива (ЕС) 2019/1937", "HinSch-RL"]},
    {"celex": "32022L2464", "bg": "Директива за отчитане на корпоративната устойчивост", "en": "CSRD",
     "aliases": ["CSRD", "Corporate Sustainability Reporting Directive", "Директива (ЕС) 2022/2464"]},
    {"celex": "32024L1760", "bg": "Директива за дължимата грижа на предприятията", "en": "CSDDD",
     "aliases": ["CSDDD", "CS3D", "Corporate Sustainability Due Diligence Directive",
                 "Директива (ЕС) 2024/1760"]},
    {"celex": "32008R0593", "bg": "Регламент Рим I", "en": "Rome I",
     "aliases": ["Rome I", "Рим I", "Регламент (ЕО) 593/2008"]},
    {"celex": "32012R1215", "bg": "Регламент Брюксел Ia", "en": "Brussels Ia",
     "aliases": ["Brussels Ia", "Брюксел Ia", "Регламент (ЕС) 1215/2012", "EuGVVO"]},
    {"celex": "32019L0882", "bg": "Директива за достъпността", "en": "European Accessibility Act",
     "aliases": ["European Accessibility Act", "EAA", "Директива (ЕС) 2019/882"]},
    {"celex": "32024L2853", "bg": "Директива за отговорността за дефектни продукти",
     "en": "Product Liability Directive",
     "aliases": ["Product Liability Directive", "PLD", "Директива (ЕС) 2024/2853"]},
]

# Judgments of the Court of Justice, CELEX of the judgment itself (Bulgarian text available).
KNOWN_CASES: List[Dict[str, Any]] = [
    {"celex": "62018CJ0311", "name": "Schrems II", "aliases": ["Schrems II", "Шремс II", "C-311/18"]},
    {"celex": "62014CJ0362", "name": "Schrems I", "aliases": ["Schrems I", "Шремс I", "C-362/14"]},
    {"celex": "62012CJ0131", "name": "Google Spain", "aliases": ["Google Spain", "C-131/12"]},
    {"celex": "62015CJ0582", "name": "Tele2 Sverige", "aliases": ["Tele2", "C-203/15", "C-698/15"]},
]

_CELEX = re.compile(r"^(?:celex[:\s]*)?([0-9])(\d{4})([A-Z]{1,2})(\d{4})(?:-(\d{8}))?$", re.I)
_NUMBERED = re.compile(
    r"(регламент|verordnung|vo|regulation|директива|richtlinie|rl|directive|решение|beschluss|decision)"
    r"\s*\((eu|еu|ес|eg|ewg|еио|eu,\s*euratom|ec|eec|ео|ecsc)\)\s*(?:nr\.?|no\.?|nо\.?|№|бр\.?)?\s*(\d+)/(\d+)",
    re.I)
_OLD_DIR = re.compile(r"(директива|richtlinie|rl|directive)\s*(\d{2,4})/(\d+)/(ес|ео|eg|ewg|ec|eec|eu)", re.I)
_TYPE = {"регламент": "R", "verordnung": "R", "vo": "R", "regulation": "R",
         "директива": "L", "richtlinie": "L", "rl": "L", "directive": "L",
         "решение": "D", "beschluss": "D", "decision": "D"}


def _key(name: str) -> str:
    return re.sub(r"[\s._()\-\u2013\u2014,\u201e\u201c\u00ab\u00bb'\"]+", "", fold(name))


def _known_index() -> Dict[str, Dict[str, Any]]:
    idx: Dict[str, Dict[str, Any]] = {}
    for act in KNOWN_ACTS:
        for name in [act["bg"], act["en"], act["celex"], *act["aliases"]]:
            idx[_key(name)] = act
    return idx


_KNOWN = _known_index()


def _year4(y: str) -> str:
    if len(y) == 4:
        return y
    n = int(y)
    return str(1900 + n if n > 50 else 2000 + n)


def match_eu(name: str) -> Optional[Dict[str, Any]]:
    """Recognise an EU act: alias (ОРЗД, GDPR, AI Act), CELEX, or 'Регламент (ЕС) 2016/679'."""
    if not name:
        return None
    raw = " ".join(name.split())
    act = _KNOWN.get(_key(raw))
    if act:
        return dict(act)
    m = _CELEX.match(raw.replace(" ", ""))
    if m and m.group(1) == "3":
        celex = f"3{m.group(2)}{m.group(3).upper()}{m.group(4)}"
        return dict(_KNOWN.get(_key(celex)) or {"celex": celex, "bg": celex, "en": celex, "aliases": []})
    for rx, kind in ((_NUMBERED, "numbered"), (_OLD_DIR, "olddir")):
        m = rx.search(raw)
        if not m:
            continue
        if kind == "numbered":
            typ, a, b = _TYPE[m.group(1).lower()], m.group(3), m.group(4)
            if len(a) == 4 and 1950 <= int(a) <= 2100 and not (typ == "R" and len(b) == 4 and int(b) < 2015):
                year, num = a, b
            else:
                year, num = _year4(b), a
        else:
            typ, year, num = "L", _year4(m.group(2)), m.group(3)
        celex = f"3{year}{typ}{int(num):04d}"
        return dict(_KNOWN.get(_key(celex)) or {"celex": celex, "bg": raw, "en": raw, "aliases": []})
    return None


def search_known(query: str) -> List[Dict[str, Any]]:
    q = fold(query)
    out = []
    for act in KNOWN_ACTS:
        names = [act["bg"], act["en"], *act["aliases"], act["celex"]]
        if any(q and (q in fold(n) or fold(n) == q) for n in names):
            if act not in out:
                out.append(act)
    m = match_eu(query)
    if m and all(a["celex"] != m["celex"] for a in out):
        out.insert(0, m)
    return out


def match_case(name: str) -> Optional[Dict[str, Any]]:
    """A judgment of the Court of Justice by its known name, its case number or a 6-prefixed CELEX."""
    raw = " ".join((name or "").split())
    if not raw:
        return None
    key = _key(raw)
    for case in KNOWN_CASES:
        if key in {_key(case["name"]), _key(case["celex"]), *(_key(a) for a in case["aliases"])}:
            return dict(case)
    m = _CELEX.match(raw.replace(" ", ""))
    if m and m.group(1) == "6":
        celex = f"6{m.group(2)}{m.group(3).upper()}{m.group(4)}"
        return dict(next((c for c in KNOWN_CASES if c["celex"] == celex), {"celex": celex, "name": celex, "aliases": []}))
    return None


# ---------------------------------------------------------------- fetching

def _parse_sparql(body: bytes, _resp: Response) -> List[str]:
    try:
        data = json.loads(body.decode("utf-8"))
        return [b["celex"]["value"] for b in data["results"]["bindings"]]
    except (ValueError, KeyError, TypeError) as err:
        raise LawError("bad_response", f"The EU version list could not be read: {err}") from err


def _parse_doc(body: bytes, resp: Response) -> Dict[str, Any]:
    ctype = resp.headers.get("content-type", "")
    if "html" not in ctype:
        raise LawError("bad_response", f"The Publications Office sent {ctype or 'no content type'} instead of XHTML.")
    text = body.decode("utf-8", errors="replace")
    root, ids = parse_html(text)
    units: Dict[str, Dict[str, str]] = {}
    for uid, node in ids.items():
        m = re.fullmatch(r"(art|rct|anx)_([0-9]+[a-z]*|[IVXLC]+[a-z]*)", uid)
        if not m:
            continue
        units[uid] = {"text": _strip_markers(html_to_text(node))}
    head = html_to_text(root)
    cut = head.find("\n▼B")
    facts = header_facts(head[:cut if cut > 0 else 6000][:12000])
    return {"units": units, "facts": facts, "cellar_url": resp.url}


_MARKERS = re.compile(r"\s*[►▼◄]\s*(?:[A-Z]\d*\b)?")
_CLOSE_BEFORE_PUNCT = re.compile(r"\s*◄\s*(?=[;,.:)\]])")


def _strip_markers(text: str) -> str:
    """Remove the consolidation markers (►B, ▼M1, ◄) without touching the words."""
    text = _CLOSE_BEFORE_PUNCT.sub("", text)
    text = _MARKERS.sub(" ", text)
    return "\n".join(" ".join(line.split()) if not line.startswith(" ") else line.rstrip()
                     for line in text.split("\n")).strip()


def _parse_judgment(body: bytes, resp: Response) -> Dict[str, Any]:
    ctype = resp.headers.get("content-type", "")
    if "html" not in ctype:
        raise LawError("bad_response", f"The Publications Office sent {ctype or 'no content type'} instead of XHTML.")
    text = body.decode("utf-8", errors="replace")
    root, ids = parse_html(text)
    points: Dict[str, str] = {}
    # A point's number cell is anchored with id="pointN", but the Court splits long points over
    # several following table rows that carry no anchor. Slicing from one anchor to the next keeps
    # the whole point instead of its first row.
    anchors = [(m.group(1), m.start()) for m in re.finditer(r'id="point(\d+)"', text)]
    for index, (number, start) in enumerate(anchors):
        end = anchors[index + 1][1] if index + 1 < len(anchors) else len(text)
        chunk_start = text.rfind("<", 0, start)
        chunk_root, _chunk_ids = parse_html(text[chunk_start if chunk_start >= 0 else start:end])
        body_text = html_to_text(chunk_root).strip()
        body_text = re.sub(r"^[\s|]*%s[\s|.]+" % number, "", body_text).strip()
        if body_text:
            points[number] = body_text
    full = html_to_text(root)
    head = " ".join(full.split()[:60])
    return {"points": points, "text": _strip_markers(full), "head": head, "cellar_url": resp.url}


class EuLaw:
    def __init__(self, store: Store):
        self.store = store

    def versions(self, celex: str) -> List[str]:
        base = celex[1:]  # 32016R0679 -> 2016R0679
        query = ("PREFIX cdm: <http://publications.europa.eu/ontology/cdm#> "
                 "SELECT DISTINCT ?celex WHERE { ?w cdm:resource_legal_id_celex ?celex . "
                 f"FILTER(STRSTARTS(STR(?celex), \"0{base}-\")) }} ORDER BY DESC(?celex) LIMIT 50")
        url = SPARQL + "?" + urllib.parse.urlencode({"query": query, "format": "application/sparql-results+json"})
        last: Optional[LawError] = None
        for _ in range(2):  # the endpoint answers 502 now and then
            try:
                got = self.store.fetch(f"p{PARSE_VERSION}-eu-versions-{celex}", url, _parse_sparql,
                                       headers={"Accept": "application/sparql-results+json"},
                                       max_bytes=2 * 1024 * 1024)
                return [c for c in got.data if re.fullmatch(r"0\d{4}[A-Z]{1,2}\d{4}-\d{8}", c)]
            except LawError as err:
                last = err
                if err.kind != "upstream":
                    break
        raise last or LawError("upstream", "The EU version list is unavailable.")

    def pick_version(self, celex: str) -> Tuple[str, Optional[str], Optional[str]]:
        """(document id, consolidated date or None, note). Latest consolidation not in the future."""
        try:
            versions = self.versions(celex)
        except LawError as err:
            return celex, None, f"Consolidated versions could not be listed ({err.message}); original text used."
        today = time.strftime("%Y%m%d")
        valid = sorted(v for v in versions if v[-8:] <= today)
        if not valid:
            return celex, None, "No consolidated version exists; this is the text as published in the Official Journal."
        best = valid[-1]
        return best, f"{best[-8:-4]}-{best[-4:-2]}-{best[-2:]}", None

    def document(self, doc_id: str, lang: str):
        return self.store.fetch(
            f"p{PARSE_VERSION}-eu-doc-{doc_id}-{lang}", CELLAR + doc_id, _parse_doc,
            headers={"Accept": "application/xhtml+xml", "Accept-Language": LANG_CODES.get(lang, "bul")},
            immutable=True)

    def judgment(self, celex: str, lang: str):
        return self.store.fetch(
            f"p{PARSE_VERSION}-eu-case-{celex}-{lang}", CELLAR + celex, _parse_judgment,
            headers={"Accept": "application/xhtml+xml", "Accept-Language": LANG_CODES.get(lang, "bul")},
            immutable=True)


def unit_id(kind: str, number: str) -> Optional[str]:
    if kind == "recital":
        return f"rct_{int(number)}" if number.isdigit() else None
    if kind == "annex":
        n = number.upper()
        if n.isdigit():
            n = int_to_roman(int(n))
        return f"anx_{n}" if roman_to_int(n) else None
    return f"art_{number.lower()}"


def human_url(doc_id: str, lang: str) -> str:
    return f"https://eur-lex.europa.eu/legal-content/{'BG' if lang == 'bg' else 'EN'}/TXT/?uri=CELEX:{doc_id}"


def header_facts(raw: str) -> Dict[str, Any]:
    """Title, Official Journal reference, amendments and corrigenda from the document head.
    ``raw`` still carries the consolidation markers (►B, ►M1, ►C1)."""
    facts: Dict[str, Any] = {}
    lines = [ln.strip() for ln in raw.split("\n")]
    for ln in lines:
        if (ln.startswith("Този текст е само за информация") or ln.startswith("Този текст служи само")
                or ln.startswith("Dieser Text dient lediglich") or ln.startswith("This text is meant purely")):
            facts["legal_effect"] = ln
            break
    base = next((ln for ln in lines if re.match(r"^►B\s*\|", ln)), "")
    if base:
        body = base.split("|", 1)[1].strip()
        m = re.search(r"\((ОВ|ABl\.|OJ)\s+L[^()]*\)\s*$", body)
        if m:
            facts["official_journal"] = m.group(0).strip()[1:-1]
            body = body[:m.start()].strip()
        facts["title"] = body
    else:
        first = lines[0] if lines else ""
        cells = [c.strip() for c in first.split("|")]
        if len(cells) == 4 and re.match(r"^L\s", cells[3]):
            facts["official_journal"] = f"{cells[2]} {cells[3]}, {cells[0]}".strip()
        i = next((k for k, ln in enumerate(lines)
                  if re.match(r"^(РЕГЛАМЕНТ|ДИРЕКТИВА|РЕШЕНИЕ|VERORDNUNG|RICHTLINIE|BESCHLUSS|REGULATION|DIRECTIVE|DECISION)\b", ln)), None)
        if i is not None:
            facts["title"] = " ".join(lines[i:i + 3])
    amended = [ln for ln in lines if re.match(r"^►M\d+\s*\|", ln)]
    corrected = [ln for ln in lines if re.match(r"^►C\d+\s*\|", ln)]
    if amended:
        facts["amended_by"] = [_row(ln) for ln in amended[:20]]
    if corrected:
        facts["corrected_by"] = [_row(ln) for ln in corrected[:20]]
    return facts


def _row(line: str) -> str:
    cells = [c.strip() for c in line.lstrip("►").split("|")]
    return f"{cells[0]}: " + ", ".join(c for c in cells[1:] if c)
