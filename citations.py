"""Parse Bulgarian and EU citation forms.

Bulgarian: 'чл. 344, ал. 2 КТ', 'чл. 344, ал. 1, т. 3 от Кодекса на труда',
'чл. 6, ал. 1, б. „е“ ОРЗД', 'КТ чл. 344', '§ 1 ЗЗД', 'чл. 5 от Конституцията'.
EU: 'чл. 6, пар. 1, б. „е“ ОРЗД', 'Art. 6(1)(f) GDPR', 'Art. 50 AI Act',
'съображение 47 ОРЗД', 'приложение III Регламент за ИИ'.
"""

from __future__ import annotations

import re
from typing import Dict, Optional

# Kind markers, longest first so 'членове' wins over 'член'.
_KIND_WORDS = [
    (r"членове|члена|член|чл\.?", "article"),
    (r"articles?|artikel|art\.?", "article"),
    (r"§|параграфи|параграф", "paragraph_section"),
    (r"съображения|съображение|recitals?", "recital"),
    (r"приложения|приложение|annexes|anhang|anlage|annex", "annex"),
]
_KIND_RE = "|".join(f"(?:{w})" for w, _ in _KIND_WORDS)
_NUM = r"(?:\d+[а-яa-z]{0,2}\b|[ivxlc]+(?=[\s,.]))"
_PROV = re.compile(rf"(?<![\w§])(?P<kind>{_KIND_RE})\s*(?P<num>{_NUM})", re.I)
_BARE = re.compile(r"^\s*(?P<num>\d+[а-яa-z]{0,2})\b\s*(?P<rest>.*)$", re.I)

# Pins read after the provision number, in the order they appear.
_PIN = [
    ("paragraph", re.compile(r"^[\s,;]*ал(?:\.|инея|инеи)?\s*(\d+[а-яa-z]?)", re.I)),
    ("paragraph", re.compile(r"^[\s,;]*пар(?:\.|аграф(?:а|и|ът)?)\s*(\d+[а-яa-z]?)", re.I)),
    ("paragraph", re.compile(r"^[\s,;]*para(?:graph)?\.?\s*(\d+[а-яa-z]?)", re.I)),
    ("paragraph", re.compile(r"^[\s,;]*\((\d+[а-яa-z]?)\)")),
    ("point", re.compile(r"^[\s,;]*т(?:\.|очка|очки)\s*(\d+[а-яa-z]?)", re.I)),
    ("point", re.compile(r"^[\s,;]*(?:no\.?|nr\.?|number)\s*(\d+[а-яa-z]?)", re.I)),
    ("letter", re.compile(r"^[\s,;]*б(?:\.|уква|укви)\s*[„\"'«(]?\s*([а-яa-z]{1,2})\b", re.I)),
    ("letter", re.compile(r"^[\s,;]*(?:lit\.?|letter|buchst\.?)\s*([а-яa-z]{1,2})\b", re.I)),
    ("letter", re.compile(r"^[\s,;]*[„\"'«]\s*([а-яa-z]{1,2})\s*[“\"'»]")),
    ("letter", re.compile(r"^[\s,;]*\(\s*([а-яa-z]{1,2})\s*\)")),
]
_LEAD = re.compile(r"^[\s,;:.]*(?:(?:от|отъ|на|в|по|to|of|the|der|des|im|in|vom|from)\s+)+", re.I)
_TRAIL = re.compile(r"[\s,;:.]+$")

# Latin letters a Bulgarian user types for the Cyrillic lettering (а, б, в, г, д ...).
_LETTER_MAP = {
    "a": "а", "b": "б", "v": "в", "g": "г", "d": "д", "e": "е", "zh": "ж", "z": "з",
    "i": "и", "y": "й", "k": "к", "l": "л", "m": "м", "n": "н", "o": "о", "p": "п",
    "r": "р", "s": "с", "t": "т", "u": "у", "f": "ф", "h": "х", "ts": "ц", "ch": "ч",
    "sh": "ш", "sht": "щ", "yu": "ю", "ya": "я", "q": "я", "w": "в", "x": "х", "c": "ц",
}


def cyrillic_letter(letter: str) -> str:
    """'a' -> 'а', 'g' -> 'г', 'e' -> 'е'; a Cyrillic letter comes back unchanged."""
    low = (letter or "").strip().lower()
    if not low:
        return ""
    if re.fullmatch(r"[а-я]", low):
        return low
    return _LETTER_MAP.get(low, low)


def norm_number(num: str) -> str:
    """Article number with its Cyrillic suffix: '2a' -> '2а', ' 3 Г ' -> '3г'."""
    text = re.sub(r"\s+", "", (num or "")).lower()
    m = re.match(r"^(\d+)([а-яa-z]{0,2})$", text)
    if not m:
        return text
    return m.group(1) + cyrillic_letter(m.group(2)) if m.group(2) else m.group(1)


def _read_pins(rest: str, out: Dict[str, str]) -> str:
    changed = True
    while changed:
        changed = False
        for key, rx in _PIN:
            m = rx.match(rest)
            if m:
                value = m.group(1)
                # The letter is kept as typed; a Bulgarian citation maps it when it is rendered.
                out.setdefault(key, value.lower() if key == "letter" else norm_number(value))
                rest = rest[m.end():]
                changed = True
                break
    return rest


def parse_citation(text: str) -> Dict[str, str]:
    """Return {'kind','number','paragraph','point','letter','law'} (keys only when found)."""
    raw = " ".join((text or "").replace("\u00a0", " ").replace("\u2011", "-").split())
    out: Dict[str, str] = {}
    m = _PROV.search(raw)
    if m:
        word = m.group("kind").lower()
        for pattern, kind in _KIND_WORDS:
            if re.fullmatch(pattern, word, re.I):
                out["kind"] = kind
                break
        num = m.group("num").strip()
        out["number"] = num.upper() if out.get("kind") == "annex" else norm_number(num)
        before = raw[:m.start()]
        after = _read_pins(raw[m.end():], out)
        law = after if _LEAD.sub("", after).strip() else before
        if law is after and before.strip() and not after.strip():
            law = before
    else:
        b = _BARE.match(raw)
        if b:
            out["number"] = norm_number(b.group("num"))
            law = _read_pins(b.group("rest"), out)
        else:
            law = raw
    law = _TRAIL.sub("", _LEAD.sub("", law)).strip()
    law = law.strip("„“\"'()").strip()
    if law:
        out["law"] = law
    return out


def split_section(section: Optional[str]) -> Dict[str, str]:
    """A section argument on its own: '344', 'чл. 344', 'чл. 344, ал. 2', 'Art. 6'."""
    if section is None:
        return {}
    if isinstance(section, (int, float)):
        return {"number": norm_number(str(int(section)))}
    parsed = parse_citation(str(section))
    parsed.pop("law", None)
    if "number" not in parsed:
        word = str(section).strip().lower()
        if word:
            parsed["label"] = word
    return parsed


ROMAN = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100}


def roman_to_int(text: str) -> Optional[int]:
    text = (text or "").lower()
    if not text or any(ch not in ROMAN for ch in text):
        return None
    total = 0
    for i, ch in enumerate(text):
        val = ROMAN[ch]
        if i + 1 < len(text) and ROMAN[text[i + 1]] > val:
            total -= val
        else:
            total += val
    return total


def int_to_roman(num: int) -> str:
    pairs = [(100, "C"), (90, "XC"), (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]
    out = ""
    for val, sym in pairs:
        while num >= val:
            out += sym
            num -= val
    return out
