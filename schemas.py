"""Tool schemas: what the model reads to decide which tool to call and how.

The first sentence of each description is what Hermes shows in the Tool Search
listing (about 60 characters), so it names the domain and the action.
"""

_LAW = {
    "type": "string",
    "description": (
        "The act, written any way a person would: Bulgarian abbreviation (КТ, НК, ГПК, ЗЗД, ТЗ, ЗЗЛД, "
        "Конституция), full title (Кодекс на труда, Закон за задълженията и договорите), a corpus id "
        "(kodeks-na-truda), or an EU act (ОРЗД/GDPR, Регламент за ИИ/AI Act, DSA, NIS2, DORA, "
        "a CELEX number like 32016R0679, or 'Регламент (ЕС) 2016/679')."
    ),
}

GET = {
    "name": "bg_law_get",
    "description": (
        "Bulgarian and EU law: read one provision word for word. "
        "Read-only. Returns the text of one article of a Bulgarian act (закон, кодекс, наредба, правилник, "
        "Конституцията) with its алинея pinpoint, the Държавен вестник reference, the act's status and an "
        "attribution line, or of an article of an EU regulation or directive (ОРЗД/GDPR, Регламент за ИИ) in "
        "Bulgarian or English. Pass the citation as the user wrote it: 'чл. 344, ал. 2 КТ', "
        "'чл. 6, ал. 1, б. „е“ ЗЗЛД', 'чл. 5 от Конституцията', 'чл. 6, пар. 1, б. „е“ ОРЗД', "
        "'Art. 50 AI Act', 'съображение 47 ОРЗД', 'приложение III Регламент за ИИ'. "
        "If you did not call this tool, say so and do not write a provision from memory. "
        "Show source.attribution with any text you quote. It returns statute text, not legal advice."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "citation": {
                "type": "string",
                "description": "Full citation in Bulgarian or English form: 'чл. 344, ал. 1 КТ', 'чл. 6 ОРЗД'.",
            },
            "law": _LAW,
            "article": {
                "type": "string",
                "description": "Article number when the act is passed separately: '344', 'чл. 2а', 'Art. 6', "
                               "'§ 1', 'съображение 47', 'приложение III'.",
            },
            "paragraph": {
                "type": "string",
                "description": "Optional алинея (EU: параграф) number to return only that paragraph, e.g. '1' or '2'.",
            },
            "as_of": {
                "type": "string",
                "description": "Bulgarian acts only: read the version in force on this date, YYYY-MM-DD. "
                               "Only works where the corpus has a reconstructed history.",
            },
            "language": {
                "type": "string",
                "enum": ["bg", "en"],
                "description": "EU acts only: text language (default from the plugin setting eu_language). "
                               "Bulgarian acts exist in Bulgarian only.",
            },
            "max_chars": {
                "type": "integer", "minimum": 500, "maximum": 30000, "default": 6000,
                "description": "Cut the text after this many characters (default 6000). Cut results say truncated=true.",
            },
        },
        "required": [],
    },
}

SEARCH = {
    "name": "bg_law_search",
    "description": (
        "Bulgarian and EU law: find an act by name or abbreviation. "
        "Read-only. Searches the Bulgarian corpus (lsource.net: 6,500+ закони, кодекси, наредби and правилници, "
        "in force and repealed, each with its latest ДВ issue) by abbreviation or title words, plus the "
        "well-known EU acts (ОРЗД, Регламент за ИИ, DSA, NIS2, DORA). Use it when the user names a topic or an "
        "unknown abbreviation; when the citation is already known ('чл. 344 КТ'), call bg_law_get directly."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Abbreviation or title words: 'КТ', 'ЗЗД', "
                                                        "'защита на потребителите', 'AI Act'."},
            "kind": {"type": "string", "description": "Optional act kind filter: 'закон', 'кодекс', "
                                                       "'наредба', 'правилник'."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
        },
        "required": ["query"],
    },
}

TOC = {
    "name": "bg_law_toc",
    "description": (
        "Bulgarian and EU law: list an act's articles or search it. "
        "Read-only. Returns the article numbers of one Bulgarian act or one EU act with the heading and the "
        "глава/раздел each article sits in, optionally filtered by a word found in a heading or in the text, "
        "with a short snippet. Use it to find which article covers a topic inside a known act, then read it "
        "with bg_law_get. The filter runs locally on the cached act."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "law": _LAW,
            "query": {"type": "string", "description": "Optional word or phrase to find in headings or text, "
                                                        "e.g. 'уволнение'."},
            "language": {"type": "string", "enum": ["bg", "en"], "description": "EU acts only: language."},
            "offset": {"type": "integer", "minimum": 0, "default": 0},
            "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
        },
        "required": ["law"],
    },
}

HISTORY = {
    "name": "bg_law_history",
    "description": (
        "Bulgarian law: the ДВ amendment history of one act. For an EU act it returns the consolidated versions instead. "
        "For a Bulgarian act the full ДВ promulgation chain; for an EU act (ОРЗД, Регламент за ИИ, a CELEX) the consolidated versions the Publications Office publishes. "
        "Read-only. Returns the full promulgation chain of a Bulgarian act (обн. and every изм./доп. with the "
        "Държавен вестник issue and date, parsed from the act itself), the corpus status (in force, repealed, "
        "not yet in force), the dates for which a point-in-time text exists, and the ДВ link. Use it to answer "
        "'what is the current version', 'when did this change' or to check whether an as_of read is possible."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "law": _LAW,
            "limit": {"type": "integer", "minimum": 1, "maximum": 300, "default": 40,
                      "description": "How many of the most recent gazette entries to return (default 40)."},
        },
        "required": ["law"],
    },
}

CASE_SEARCH = {
    "name": "bg_case_search",
    "description": (
        "Bulgarian courts: find Constitutional Court acts. "
        "Read-only. Searches the published acts of the Bulgarian Constitutional Court (Конституционен съд) by "
        "case number (к.д. № 15/2026), act type (решение, определение, становище), date range and keywords. "
        "Returns doc_id, type, date, case number and the reporting judge; read one with bg_case_get. "
        "Keywords are searched by fetching the acts themselves, so narrow the candidates with a date or type "
        "first. Lower courts and the Supreme Courts are not covered."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "case_number": {"type": "string", "description": "Constitutional case number: '15/2026', '33/2024'."},
            "query": {"type": "string", "description": "Words to find inside the acts, e.g. 'данък', "
                                                        "'изборен кодекс'."},
            "act_type": {"type": "string", "description": "Exact act type: 'решение', 'определение', "
                                                           "'становище по решение', 'особено мнение по решение'. Call without it to see the types in `act_types`."},
            "date_from": {"type": "string", "description": "Earliest act date, YYYY-MM-DD."},
            "date_to": {"type": "string", "description": "Latest act date, YYYY-MM-DD."},
            "scan_limit": {"type": "integer", "minimum": 1, "maximum": 25, "default": 8,
                           "description": "How many candidate acts a keyword search may download (default 8)."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
        },
        "required": [],
    },
}

CASE_GET = {
    "name": "bg_case_get",
    "description": (
        "Bulgarian and EU courts: read one decision. "
        "Read-only. Returns one act of the Bulgarian Constitutional Court by doc_id or case number, or one "
        "judgment of the Court of Justice of the European Union in Bulgarian or English by CELEX number, case "
        "number (C-311/18) or known name (Schrems II). Pass paragraph to return a single numbered point of a "
        "CJEU judgment. Show source.attribution with any text you quote. It returns the court's text, not "
        "legal advice."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "doc_id": {"type": "string", "description": "Constitutional Court act id from bg_case_search, "
                                                         "e.g. 'act-10457'."},
            "case_number": {"type": "string", "description": "Constitutional case number, e.g. '15/2026'."},
            "celex": {"type": "string", "description": "CJEU judgment: '62018CJ0311', 'C-311/18' or 'Schrems II'."},
            "paragraph": {"type": "string", "description": "CJEU only: one numbered point of the judgment, e.g. '201'."},
            "language": {"type": "string", "enum": ["bg", "en"], "description": "CJEU only: language."},
            "max_chars": {"type": "integer", "minimum": 500, "maximum": 60000, "default": 8000,
                          "description": "Cut the text after this many characters (default 8000)."},
        },
        "required": [],
    },
}

ALL_SCHEMAS = [GET, SEARCH, TOC, HISTORY, CASE_SEARCH, CASE_GET]
