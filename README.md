<div align="center">

<img src="docs/banner.png" alt="bg-law: Bulgarian and EU law, word for word. Hermes Agent plugin" width="100%">

</div>

# bg-law

A Hermes plugin for Bulgarian and EU law. It reads the provision before answering and returns the part
that makes the answer checkable: the Държавен вестник issue.

Ask for `чл. 344, ал. 1 КТ` and it comes back with the text of that алинея, the chapter and section it
sits under, the status of the act, the last amendment (ДВ бр. 89/2026 от 29.09.2026), a link to the
source page, and an attribution line ready to paste. A Bulgarian provision quoted without its issue
cannot be checked by anyone, and a model answering from memory will produce the number and the issue
with the same confidence. Removing that failure mode is the whole point.

The same rule runs through the other two jurisdictions. EU acts are read from the Publications Office in
Bulgarian or English, in the consolidated version where one exists, so `чл. 6, пар. 1, б. „е“ ОРЗД` and
`Art. 6(1)(f) GDPR` return the same provision in two languages. Case law is read, not summarised: an act
of the Конституционен съд comes back in full, and a judgment of the Court of Justice can be read one
numbered point at a time, as in `Schrems II`, point 201.

Texts are cached on disk, so a second question about the same act costs nothing and a network outage
does not take the answers with it.

## Install

```
hermes plugins install apoapostolov/hermes-bg-eu-law
hermes plugins enable bg-law
```

Bulgarian statute text needs a free lsource.net key. Register at
[lsource.net/developers](https://lsource.net/developers), create a key, and paste it into
Settings, Plugins, bg-law. It is stored in `.env` as `LSOURCE_API_KEY` and never in `config.yaml`. The
quota is 60 requests a minute and 5,000 a day, which the plugin stays clear of with one request per
second and the cache.

Hermes loads plugin code at startup, so the six tools appear in the next session.

## What it accepts

Citations go in as people write them, not in a form invented by the schema: `чл. 344, ал. 2, т. 3 КТ`,
`§ 1 ЗЗД`, `чл. 5 от Конституцията`, `чл. 6, ал. 1 от Закона за защита на личните данни`, `КТ чл. 344`,
`Art. 6(1)(f) GDPR`, `съображение 47 ОРЗД`, `приложение III Регламент за ИИ`. A Latin letter typed for a
Cyrillic suffix is understood, so `чл. 2a` finds `чл. 2а`, and the definite article does not matter:
`Кодекса на труда` resolves to the same act as `Кодекс на труда`.

The 25 abbreviations Bulgarian lawyers actually write are resolved directly, among them КТ, НК, НПК,
ГПК, АПК, ЗЗД, ТЗ, СК, ЗН, ЗС, КСО, ЗДДС, ДОПК, ЗУТ, ЗЗЛД and ЗОП. Each row in that table was checked
against the live corpus, because a wrong abbreviation silently returns a plausible wrong act.

## The six tools

| Tool | What it does |
| --- | --- |
| `bg_law_get` | One article and one алинея of a Bulgarian act, or one article, recital or annex of an EU act |
| `bg_law_search` | An act by abbreviation or title word, plus the well-known EU acts |
| `bg_law_toc` | The act's articles with the глава and раздел each sits in, filterable by a word, with a snippet |
| `bg_law_history` | The full ДВ promulgation chain, the act's status, and the dates a point-in-time text exists for |
| `bg_case_search` | Constitutional Court acts by case number, act type, date range or keyword |
| `bg_case_get` | One Constitutional Court act, or one numbered point of a CJEU judgment |

A result looks like this (trimmed):

```json
{
 "ok": true,
 "jurisdiction": "BG",
 "law": {"id": "kodeks-na-truda", "title": "Кодекс на труда", "abbreviation": "КТ"},
 "provision": {
  "citation": "чл. 344, ал. 1 КТ",
  "scope": "Глава шестнадесета. ПРЕКРАТЯВАНЕ НА ТРУДОВОТО ПРАВООТНОШЕНИЕ / Раздел IV. Защита срещу незаконно уволнение",
  "text": "(1) Работникът или служителят има право да оспорва законността на уволнението ..."
 },
 "version": {"status": "in_force", "issue": "ДВ бр. 89/2026", "changed": "2026-09-29",
             "official": false, "gazette_count": 129},
 "source": {"url": "https://lsource.net/laws/kodeks-na-truda", "license": "CC BY 4.0",
            "attribution": "Източник: Питай законите (lsource.net) ..."}
}
```

## Where the text comes from

Bulgaria has no official consolidated-legislation API, and the alternatives are worse than they look.
Държавен вестник publishes each issue, not a consolidated act, and its per-act page carries the title,
the issue, the section and the page number but no body text. parliament.bg serves a 4 KB
client-rendered shell for a law, with the text arriving from JavaScript. lex.bg, the free consolidator
most people use, answers with `User-agent: * Disallow: /`, so it is not touched.

That leaves [lsource.net](https://lsource.net), which reconstructs the consolidated text from the State
Gazette and publishes it under CC BY 4.0: 6,585 Bulgarian acts in force, 13,629 including the repealed
ones, with the amendment history and a text as of a past date where the reconstruction reaches back. It
needs the free key, and it marks every record `official: false`, which the plugin repeats in every answer.

One consequence worth knowing before scraping it by hand: the public act page at `/laws/<id>` is a free
preview that stops mid-sentence. Кодекс на труда ends inside чл. 162. Only the API serves the whole act,
so the plugin reads the API and never the page.

EU law and judgments come from the Publications Office of the EU (CELLAR) through content negotiation:
XHTML in `bul` or `eng`, with the ELI structure intact (`art_6`, `rct_47`, `anx_III`, and `point201` for
a judgment), which is what makes article-level extraction possible at all. Constitutional Court acts come
from `constcourt.bg/search-acts`, a single document listing 951 published acts with their case numbers,
dates and reporting judges, plus a full-text page per act.

## What is not covered

The Supreme Courts and the lower courts. vks.bg is a JavaScript application with no discoverable
endpoint, and the ВСС portal that indexes all courts gates its search behind a CAPTCHA, so a scrape would
be both fragile and unwelcome. Municipal наредби sit on lsource's own site but not in its free API. The
EU treaties return no ELI-structured text from the Publications Office, so article-level extraction fails
on them.

Where a source is missing, the tool says so and links what exists. It does not fill the gap from memory.

## When something goes wrong

The plugin treats a missing answer as a result, not as an invitation. `чл. 9999 КТ` returns
`article_not_found` with the instruction to list the act's articles, never a neighbouring number. A
withheld text (HTTP 409, where the corpus audit flagged an act) comes back as `withheld` with the advice
not to quote it. A missing key returns `key_required` with the registration link.

`as_of` works only where the corpus has a reconstructed history, and that varies per act: Кодекс на труда
carries two versions from 31.07.2026, Наказателен кодекс carries none. When the date asked for is older
than the reconstruction reaches, the answer falls back to the current text and says so, rather than
pretending to a snapshot it does not have.

If a source cannot be reached, the last cached copy is served with `stale: true` and the date it was
fetched. Text cut at `max_chars` reports `truncated: true` with the character count, so a partial quote is
never presented as the whole provision.

## Settings

| Key | Default | Meaning |
| --- | --- | --- |
| `lsource_api_key` | empty | Free key from lsource.net/developers. Stored in `.env` as `LSOURCE_API_KEY`. |
| `language` | `auto` | Language of the attribution line: `auto`, `bg` or `en`. |
| `eu_language` | `bg` | Default language of EU texts. |
| `max_age_hours` | 24 | Cached acts older than this are revalidated with the source. `0` = always. |
| `cache_max_mb` | 200 | Oldest cached texts are dropped beyond this. |
| `case_law` | true | Allow `bg_case_search` and `bg_case_get`. |
| `eu_law` | true | Allow EU acts. |

## How this was checked

Against the live sources on 26.10.2026, on Windows 11 with Python 3.11: `чл. 344, ал. 1 КТ` (ДВ бр.
89/2026, 129 gazette entries), `чл. 2а КТ`, `чл. 12 КТ` (repealed, flagged as such), `чл. 5 от
Конституцията`, `чл. 115 НК`, `чл. 6, ал. 1 ЗЗЛД`, `чл. 9999 КТ` (`article_not_found`, no guess),
`чл. 6, пар. 1, б. „е“ ОРЗД` and `Art. 6(1)(f) GDPR` (consolidated 2016-05-04), `съображение 47 ОРЗД`,
`чл. 50` and `приложение III` of the AI Act, `bg_case_search` for к.д. 15/2026 (seven acts, ambiguous on
read, then read by `doc_id`), and `Schrems II` point 201.

`python -m unittest discover -s tests` runs 50 offline tests over response fixtures trimmed from those
real payloads, with no network. `hermes plugins validate` passes the manifest, registration, collision and
security gates (6 tools, 0 hooks). The Bulgarian parser is the part most likely to drift when the source
changes its layout, which is why the cache key carries a parser version and a parser fix invalidates
cached parses instead of serving them.

## Licence and limits

MIT, see LICENSE. The HTTP, cache and CELLAR layers derive from
[hermes-de-law](https://github.com/MadeByBrain/hermes-de-law) by MadeByBrain (MIT), credited in the
LICENSE file.

It returns legal texts, not legal advice. The State Gazette and the Official Journal bind, not this
plugin. For a real case the user needs a lawyer.
