# Changelog

All notable changes to bg-law are recorded here. The format follows Keep a Changelog and the version
follows the plugin manifest.

## [1.0.1] - 2026-10-31

A citation that resolved to the wrong act, a judgment paragraph that stopped mid-sentence and an EU
question that was refused with "not in this corpus" all had the same root cause: the parsers had never
been run against the live sources, only against fixtures. This release is the result of doing that, 184
checks at a time. Nothing about the tools changed on the surface; what changed is that the answers hold.

### Fixed

- A long act name carrying the definite article (`чл. 6, ал. 1 от Закона за защита на личните данни`)
  resolved to an unrelated наредба whose title quotes the same words. The act you name is now the act you get.
- `bg_law_history` refused every EU act, although its description promised them. It now returns the
  consolidated versions the Publications Office publishes, with the current one named.
- A Court of Justice case number (`bg_case_get(case_number="C-311/18")`) searched the Bulgarian
  Constitutional Court and found nothing. It now goes to the judgment.
- A long numbered point of a judgment came back as its first table row: point 201 of Schrems II was 82
  characters instead of the whole point.
- `bg_law_search` returned nothing for an abbreviation (`ЗЗД`), because the corpus search matches titles
  literally and no title contains those letters.
- `bg_law_toc` on an EU act listed no recitals, because consolidated texts drop them. Recitals are listed
  from the original Official Journal text, and the answer says where they came from.
- `bg_case_search(act_type="решение")` also returned `особено мнение по решение`. The filter is exact, and
  the result lists the types it knows.
- An unknown Constitutional Court id answered `ok` with the site chrome as its text; it is now
  `case_not_found`.
- `as_of` with a date between two reconstructions failed instead of serving the newest version at or
  before it; a date before coverage now names the coverage it has. Both are reported as `as_of_used` and
  `as_of_not_available` rather than as a fallback the caller has to infer.
- A refused host is reported as `host_not_allowed`, not as a network failure.

### Added

- Every successful payload carries a `disclaimer` naming the binding text, so an answer built from it can
  repeat the limit without the caller inventing one.
- `bg_case_search` returns the licence, the attribution and the legal `act_types` it accepts.
- `bg_law_get` on an EU act reports `version.celex` next to the document id.
- `scripts/live_check.py`, the harness behind this release: ten groups, real sources, throwaway cache.

### Notes

- The parser version moved to 2, so the first lookup after upgrading reparses from the source instead of
  serving text cached by the previous parsers.

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
