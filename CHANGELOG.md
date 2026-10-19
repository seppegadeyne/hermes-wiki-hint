# Changelog

## 1.5.0 (2026-10-19)

Measured on five days of cli/telegram sessions (490 page lines shown, 57 read later):

- A page match needs at least two specific overlapping terms. The single
  rare-term path is gone: those lines were read 1 time in 108.
- Image descriptions that Hermes prepends to a message are no longer matched
  against the index (1 read in 63 lines); the user's own words still are.
- A `browser_exec` step counts for a quarter towards the save nudge: 11 of
  14 nudges in 48 hours were armed (partly) by browser steps, mostly page tests
  (forms, landing pages), and only 2 of 14 nudges led to a wiki write.
- Replay: 490 -> 294 page lines (-40%) while keeping 52 of the 57 that were
  read; save nudges over the last 48 hours 13 -> 9.

## 1.4.0 (2026-10-17)

- No hints for delegated subagents and scripted `hermes chat --source tool`
  runs (`WIKI_HINT_SKIP_SOURCES`, default `subagent,tool`). In three days of
  sessions they received 110 hints and read none of the 285 pages shown.
- No page suggestions for harness-written messages (background-process
  notices, compaction summaries, the cron wrapper); reminder and cron
  instruction unchanged.
- Replay on those sessions: 396 -> 285 hinted messages, injected text -38%.
