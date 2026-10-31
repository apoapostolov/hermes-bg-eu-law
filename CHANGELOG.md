# Changelog

All notable changes to bg-law are recorded here. The format follows Keep a Changelog and the version
follows the plugin manifest.

## [Unreleased]

Found by `scripts/live_check.py`, the 184 check harness that runs against the live sources.

### Fixed

- A long act name carrying the definite article (`чл. 6, ал. 1 от Закона за защита на личните данни`)
  resolved to an unrelated наредба whose title quotes that string. The resolver now searches the
  article-free form of the name and only trusts a single search hit when it actually covers the query.
- `bg_law_search` found nothing for an abbreviation (`ЗЗД`), because the corpus search matches titles
  literally and no title contains it. The verified alias table is now consulted first.
- `bg_law_history` refused an EU act with `law_not_found`. It now returns the consolidated versions the
  Publications Office publishes, with the legal effect and the current document.
- `bg_case_get(case_number="C-311/18")` searched the Bulgarian Constitutional Court. A Court of Justice
  case number (or a known case name) now goes to the judgment reader.
- `bg_law_toc` on an EU act listed no recitals, because consolidated texts omit them. Recitals are now
  listed from the original Official Journal text, with a note saying where they came from.
- Court of Justice points were cut to the first table row, so a long point came back as a fragment. A
  point now runs from its own anchor to the next one, which keeps the continuation rows.
- `bg_case_search(act_type="решение")` also returned `особено мнение по решение`. The filter is an exact
  match, and the payload lists the `act_types` it knows so a caller can pick a valid one.
- An unknown Constitutional Court doc id answered `ok` with the site chrome as its text. A page without
  the case detail block is now `case_not_found`.
- `as_of` with a date between two reconstructions failed with `not_found`, and a date before coverage gave
  no coverage detail. It now serves the newest version at or before the date (`as_of_used`), and otherwise
  refuses with `as_of_not_available` plus the coverage it has.
- The host allowlist reported a refused host as `network`; it is now `host_not_allowed`.

### Added

- Every successful payload carries a `disclaimer` saying the texts are not legal advice and naming the
  binding version.
- `bg_case_search` carries a licence, an attribution and `act_types` with its results.
- `bg_law_get` on an EU act reports `version.celex` next to the document id.
- `scripts/live_check.py`, the live harness, and 13 offline regression tests for the defects above.

## [1.0.0] - 2026-10-26

First release: six read-only tools for Bulgarian and EU law.

### Added

- `bg_law_get` reads one article of a Bulgarian act, with алинея pinpoint and the ДВ issue, or one
  article, recital or annex of an EU act in Bulgarian or English, consolidated version where one exists.
- `bg_law_search` finds a Bulgarian act by abbreviation or title word (25 common abbreviations are
  verified against the corpus) plus the well-known EU acts.
- `bg_law_toc` lists an act's articles with the глава and раздел each sits in, filterable by a word found
  in a heading or in the text, with a snippet.
- `bg_law_history` returns the full promulgation chain parsed from the act (обн. and every изм./доп. with
  the ДВ issue and date), the act's status, and the dates a point-in-time text exists for.
- `bg_case_search` and `bg_case_get` read acts of the Bulgarian Constitutional Court by case number, act
  type, date or keyword, and CJEU judgments by CELEX, case number or name, paragraph by paragraph.
- Bulgarian citation parsing: `чл.`, `ал.`, `т.`, `б.`, `§`, `пар.`, `съображение`, `приложение`, law
  before or after the number, Latin letters typed for Cyrillic article suffixes.
- Local cache with ETag revalidation, stale-copy fallback, size cap and pruning; three hosts only, HTTPS
  only, one request per second per host, redirects followed by hand.
- Bundled `read-law` skill so the model fetches the text before answering.
- 50 offline tests over real response fixtures.
