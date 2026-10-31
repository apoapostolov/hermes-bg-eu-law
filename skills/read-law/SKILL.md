---
name: read-law
description: "Bulgarian or EU law question (чл., ал., Кодекс на труда, закон, наредба, Държавен вестник, КТ, НК, ЗЗД, ОРЗД/GDPR, AI Act, Конституционен съд, Съд на ЕС)? Read the text with the bg-law tools (bg_law_get, bg_law_toc, bg_law_history) before any web search, quote source.attribution and the ДВ issue."
---

When the user asks about a Bulgarian act, an EU regulation or directive, an act of the Bulgarian
Constitutional Court or a judgment of the Court of Justice of the EU, read the source text with the
bg-law tools before you answer. If they are not in your tool list, call `tool_search` with an English
query such as "Bulgarian law article" or "Bulgarian Constitutional Court", then `tool_call`.

- A citation like `чл. 344, ал. 1 КТ`, `чл. 6, ал. 1, б. „е“ ЗЗЛД` or `чл. 6, пар. 1, б. „е“ ОРЗД`:
  call `bg_law_get` with `citation` exactly as written.
- A topic inside a known act ("уволнение в КТ"): `bg_law_toc` with `law` and `query`, then `bg_law_get`.
- An unknown act or abbreviation: `bg_law_search` (КТ, НК, ГПК, ЗЗД, ТЗ, ЗЗЛД and the other common
  abbreviations are recognised directly).
- "When did this change", "is this still in force", "what did it say in 2024": `bg_law_history`, then
  `bg_law_get` with `as_of` for acts whose history the corpus has reconstructed. For an EU act,
  `bg_law_history` returns the consolidated versions the Publications Office publishes; pick one and pass
  its document id as `celex`, because a regulation's history is its consolidated snapshots.
- A Constitutional Court act: `bg_case_search` by case number (к.д. № 15/2026), act type or date, then
  `bg_case_get`. `act_type` is an exact match and the payload lists the valid `act_types`; a case number
  of the Court of Justice (C-311/18) belongs in `bg_case_get`, not in the Bulgarian court. A judgment of the Court of Justice: `bg_case_get` with `celex` (62018CJ0311, C-311/18
  or "Schrems II") and `paragraph` for one numbered point.

When you quote a provision or a decision, add `source.attribution` and `source.url`, and the version:
for a Bulgarian act `version.issue` (the ДВ issue and date of the last amendment, plus `gazette_latest`),
for an EU act `version.consolidated_as_of`. If `stale` is true, say the source could not be reached and
give the cache date. If `as_of` was asked, report `as_of_used` as the date the text actually belongs to;
if the tool answers `as_of_not_available`, say the reconstruction does not reach that date and offer the
coverage it lists. Every successful payload carries a `disclaimer`: keep its sense in the answer.

A web page is not a substitute: lex.bg forbids automated clients and its consolidated pages do not carry the
amendment issue, so an issue taken from a search result is a guess with a date on it. If the tools are
available and you answered a Bulgarian or EU legal question without them, you answered from memory.
If you did not call a bg-law tool, say you did not fetch the text. Do not write a provision from memory
and do not say it came from Държавен вестник, lsource.net or EUR-Lex. Never guess a nearby article number
after `article_not_found`: the answer is `bg_law_toc`, not a guess.

Bulgarian statute text comes from lsource.net, which reconstructs the consolidated text from the State
Gazette and marks it as not official; when no free key is configured the tool answers `key_required` and
tells the user where to get one. Only the text promulgated in Държавен вестник (dv.parliament.bg) and the
Official Journal of the EU bind. The plugin returns legal texts, not legal advice; for a real case the
user needs a lawyer.

На български: при въпрос за български закон, право на ЕС, акт на Конституционния съд или решение на
Съда на ЕС първо изтегли текста с инструментите на bg-law (иначе ги намери с `tool_search`), цитирай
`source.attribution` и номера на ДВ, не цитирай по памет и не измисляй съседен член. Плъгинът дава
правни текстове, не правна консултация.
