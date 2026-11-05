# Tools and settings

bg-law 1.0.2 keeps the original six tool names and adds five read-only tools. Every successful tool
response includes the legal-text disclaimer. Errors include `error_type` and a recovery `next_step`.

## Read a revision

```json
{"citation":"чл. 344, ал. 1, т. 3 КТ"}
{"citation":"чл. 344 КТ","as_of":"2026-09-29"}
{"citation":"Art. 6 GDPR","revision":"02016R0679-20160504","language":"en"}
```

These are `bg_law_get` arguments. `as_of` is for BG promulgation dates. `revision` accepts a BG hash
from history or an EU consolidated document identifier. The two selectors are mutually exclusive.
`as_of_used` is the source's actual BG revision date, which can precede the requested date.
`version.status` describes today's whole act; it does not establish historical applicability.

Historical article reads do not depend on the provision surviving in today's act. Historical `§`
reads use the dated whole document so an identically numbered article cannot be substituted.
Unavailable history returns `as_of_not_available`. Empty responses or unconfirmed identity are refused.

Point and letter citations select actual source markers. BG extraction uses Akoma Ntoso structure
when available, with a text-marker fallback when structured extraction is unavailable. Missing markers
return `point_not_found` or `letter_not_found`. Repeated provision numbers return
`ambiguous_provision`. The plugin does not decide which amendment's transitional provisions a user meant.

## Continue a long response

`bg_law_get`, `bg_case_get`, `bg_law_export` and `bg_law_changes` accept a character `offset`.
Use the returned `next_offset`, preserving the original arguments and revision. Statute text allows
up to 30,000 characters per page; court decisions, exports and diffs allow up to 60,000.
XML pages are fragments; concatenate them before parsing the XML document.

## Discover documents

`bg_law_search` accepts `query`, `limit`, `offset`, `kind` and `jurisdiction`. The default `auto`
recognizes known EU identifiers and otherwise searches lsource titles. `jurisdiction="EU"` uses
CELLAR's indexed metadata-title search. This searches titles rather than document bodies.

BG search preserves the source total and exposes `next_offset`. Recognized abbreviations are checked
against `kind` before being returned. An alias lookup has one candidate rather than an invented corpus total.

`bg_case_search` defaults to the Bulgarian Constitutional Court. It accepts case numbers, exact act
types and calendar date bounds. `query` downloads at most `scan_limit` candidates, counting failed
attempts toward the budget. `keyword_scan` identifies failures, capped coverage and
`next_scan_offset`. Continue that candidate window before interpreting an empty result as exhaustive.
Use `offset` to page the matches in a window. `court="EU"` searches CELLAR decision titles instead.

`bg_case_get` accepts a Constitutional Court `doc_id`, a CJEU/General Court CELEX or a case number.
Known joined-case aliases resolve to their canonical judgment. Other C/T case numbers are accepted
only when the downloaded judgment confirms the requested number. Use EU title search for joined
cases whose individual case number has no standalone judgment document.

## Additional lsource operations

| Tool | Arguments | Source |
| --- | --- | --- |
| `bg_law_changes` | `law`, `date_from` and `date_to`, or `revision`; optional `offset`, `max_chars` | `/acts/{id}/changes`, `/acts/{id}/diff/{hash}` |
| `bg_law_transposition` | `law`, or `query`, `limit`, `offset` | `/acts/{id}/transposition`, `/transposition` |
| `bg_law_recent` | `limit`, `offset` | `/acts/recent` |
| `bg_law_export` | `law`, `format` (`akn` or `text`), optional `as_of`, `offset`, `max_chars` | `/acts/{id}/akn`, `/acts/{id}/text` |
| `bg_law_status` | optional `check_sources` | Local diagnostics; explicit probes bypass the cache |

Change results preserve resolved source dates even when they differ from the requested range.
Transposition rows retain their source sentences and nullable EU links; they describe claims found in
the acts, not a finding that implementation is complete or correct. The recent feed has bounded coverage.

The [lsource API documentation](https://lsource.net/developers) defines authentication, quotas and
reconstruction limits. The paid clause API and bulk corpus dump are outside this plugin's tools.

## Configuration

| Setting | Default | Values |
| --- | --- | --- |
| `lsource_api_key` | empty | Secret stored as `LSOURCE_API_KEY` in the active profile's `.env` |
| `language` | `auto` | Attribution language: `auto`, `bg`, `en` |
| `eu_language` | `bg` | EU source text: `bg`, `en` |
| `max_age_hours` | `24` | Whole hours, `0` to `8760`; `0` always rechecks |
| `cache_max_mb` | `200` | Cache target, `5` to `10000` MB |
| `allow_stale` | `true` | Allow expired copies during network/source outages, with a warning |
| `bg_law`, `eu_law`, `bg_cases`, `eu_cases` | `true` | Independent source switches |
| `case_law` | `true` | Legacy combined court switch; migrated when source choices are saved |

Connection checks report missing or rejected keys, rate limits, source failures and last successful
checks without returning credentials. They do not prove the completeness or accuracy of a legal corpus.
Settings writes use the registered plugin context when available, or the public Hermes configuration
API when the dashboard starts before any chat has loaded the tools. The generic Agent settings form
is also available.

Cache entries preserve check dates and validators. Responses have `freshness` where multiple source
reads contribute to an answer, and expired fallback copies are marked `stale`. Credentials are removed
when a redirect changes source hosts. Rate limits return `retry_after`; the plugin does not immediately
repeat a throttled request.
