# Changelog

All notable changes to bg-law are recorded here. The format follows Keep a Changelog and the version
follows the plugin manifest.

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
  only, one request per second, redirects followed by hand.
- Bundled `read-law` skill so the model fetches the text before answering.
- 50 offline tests over real response fixtures.
