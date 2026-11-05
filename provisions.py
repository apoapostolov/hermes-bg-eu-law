"""Exact provision selection. A missing or ambiguous marker is never invented."""

import re
import xml.etree.ElementTree as ET

from .client import LawError
from .textutil import xml_to_text


def parse_akn(body, _response):
    try:
        root = ET.fromstring(body)
    except ET.ParseError as err:
        raise LawError("bad_response", "The source returned invalid Akoma Ntoso XML.") from err
    if root.tag.rsplit("}", 1)[-1] != "akomaNtoso":
        raise LawError("bad_response", "The source did not return an Akoma Ntoso document.")
    return {"xml": body.decode("utf-8"), "root": root.tag}


def akn_pinpoint(doc, number, parsed):
    root = ET.fromstring(doc["xml"])
    target = next((el for el in root.iter() if el.get("eId") == f"art_{number}"), None)
    if target is None:
        raise LawError("pinpoint_unavailable", "This article has no structured XML unit.",
                       "Read the whole article and verify the requested pinpoint in the source.")
    for key, tag in (("paragraph", "paragraph"), ("point", "point"), ("letter", "point")):
        want = parsed.get(key)
        if not want:
            continue
        candidates = []
        for el in target.iter():
            if el is target or el.tag.rsplit("}", 1)[-1] != tag:
                continue
            num = next((c for c in el if c.tag.rsplit("}", 1)[-1] == "num"), None)
            label = "".join(num.itertext()).strip(" .()\t\n") if num is not None else ""
            if label == str(want):
                candidates.append(el)
        if len(candidates) != 1:
            raise LawError(f"{key}_not_found", f"The XML has no unique {key} {want} in this provision.",
                           "Read the parent provision or choose a marker present in its text.")
        target = candidates[0]
    return xml_to_text(target)


def select_marker(text, value, family):
    pattern = (r"(?m)^[ \t]*\(?([0-9]+[a-zа-я]?)[.)]\s+" if family == "point"
               else r"(?m)^[ \t]*\(?([a-zа-я]{1,2})[.)]\s+")
    marks = list(re.finditer(pattern, text, re.I))
    matches = [i for i, m in enumerate(marks) if m.group(1).lower() == str(value).lower()]
    if len(matches) != 1:
        raise LawError(f"{family}_not_found", f"No unique {family} {value} exists in the selected text.",
                       "Read the parent provision and use one of its actual markers.",
                       {"available": [m.group(1) for m in marks]})
    i = matches[0]
    end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
    return text[marks[i].start():end].strip()


def pinpoint(text, parsed):
    for key in ("point", "letter"):
        if parsed.get(key):
            text = select_marker(text, parsed[key], key)
    return text


def eu_letter(letter, lang):
    alphabet = "абвгдежзийклмнопрстуфхцчшщъьюя"
    if lang == "bg" and re.fullmatch("[a-z]", letter or ""):
        return alphabet[ord(letter) - ord("a")]
    if lang == "en" and letter in alphabet:
        index = alphabet.index(letter)
        return chr(ord("a") + index) if index < 26 else letter
    return letter
