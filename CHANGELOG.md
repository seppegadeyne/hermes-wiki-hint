# Changelog

## 1.8.0 (2026-10-23)

Measured on 48 h of real sessions (166 page lines shown, 40 read): the new rules keep 37 of
the 40 read lines (1.7.1 kept 35) and show 32 fewer matches.

- Wrappers around user text (`[System note: ...]`, `[OUT-OF-BAND USER MESSAGE ...]`,
  `[STILL IN PROGRESS ...]`) are removed before matching. A gateway system note alone got
  6 page lines, none read; the user text inside a steer marker still matches.
- Bare years and `next`, `user`, `system` never count as matching terms (0 reads).
- `WIKI_HINT_EXTRA_STOPWORDS`: per-install words never used for matching.
- The save-nudge cap (3 per session) also holds after a restart or `/resume`: nudges already in
  the conversation history are counted. One long session got 7 nudges before.
- Skipped sources (subagents, tool runs) no longer keep nudge state in `post_tool_call`.

## 1.7.1 (2026-10-21)

- The install note on `approvals.cron_mode: deny` now says what it means: the upkeep's own commands
  are not flagged and run; only flagged shapes (heredocs, `xargs rm`) are refused. MAINTENANCE.md
  says to delete old backups with plain `rm`.

## 1.7.0 (2026-10-21)

- `hermes wiki-hint upkeep install|status|remove`: one command per install sets up (or updates,
  never duplicates) the weekly upkeep cron job and its two wrapper scripts; `--paused`, `--dry-run`,
  `--schedule`, `--homes all|self`, `--archive-glob`, `--deliver`. Warns when `approvals.cron_mode`
  is not `approve`.
- MAINTENANCE.md: vault backups are named with date and time and never overwritten (two runs on
  one day overwrote the first tarball in the end-to-end test).
- MAINTENANCE.md: "Working unattended" (no heredocs or `python3 -c` in cron runs; scripts + plan files).
- Tested end to end on a real install (5 profiles): a trial run brought 8 memory files from 95-106% to
  80-85% of their limit with every removed fact traced to a wiki page or skill, split 3 dated pages
  into statuslogs, fixed tags, index lines and a broken link; then the job installed by this command
  ran from the scheduler.

## 1.6.0 (2026-10-21)

- Upkeep audit: `maintenance.py` and the `/wiki-audit` slash command report, read-only, on the
  vault (index vs pages, broken wikilinks, frontmatter, tags outside the taxonomy, page size,
  log rotation, raw/ hash drift, orphans) and on `MEMORY.md` / `USER.md` of every profile (size
  against each profile's limits, long and near-duplicate entries, entries naming paths that no
  longer exist) plus `SOUL.md` size.
- MAINTENANCE.md: the playbook for a scheduled agent run that repairs what the audit lists
  (backup first, memory detail moved into the wiki with a pointer left behind, 15-page cap per
  run, re-audit, one log entry), and how to schedule it as a weekly cron job with the audit as
  script and as change monitor.
- Measured on a real vault (196 pages) and install (5 profiles): the audit runs in under a second;
  9 of 10 memory files were at 95% or more of their limit, one over it.
- `memory_edit.py`: lock-aware, exact-text, backed-up edits of memory files for the upkeep run
  (also for profiles the run is not in). The hooks never import it.
- `/wiki-audit` shows this profile's memory files only; `--all` adds other profiles without entry text.
- The hooks are unchanged.

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
