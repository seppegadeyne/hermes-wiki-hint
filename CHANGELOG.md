# Changelog

## 1.4.0 (2026-10-17)

- No hints for delegated subagents and scripted `hermes chat --source tool`
  runs (`WIKI_HINT_SKIP_SOURCES`, default `subagent,tool`). In three days of
  sessions they received 110 hints and read none of the 285 pages shown.
- No page suggestions for harness-written messages (background-process
  notices, compaction summaries, the cron wrapper); reminder and cron
  instruction unchanged.
- Replay on those sessions: 396 -> 285 hinted messages, injected text -38%.
