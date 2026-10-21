# hermes-wiki-hint

A [Hermes Agent](https://hermes-agent.nousresearch.com) plugin that nudges the
agent to actually *use* an llm-wiki (Karpathy-style knowledge base of
interlinked markdown pages) instead of re-doing external research from scratch
— and to proactively file durable findings back into the wiki.

## Why

If you keep a wiki (see the `llm-wiki` skill pattern), the agent often
forgets it exists mid-task: it searches the web for things already written
down, and it finishes a research-heavy session without saving anything.
This plugin makes the wiki visible at the moments that matter, without
writing anything itself.

## What it does

Two lifecycle hooks and one read-only slash command (`/wiki-audit`, see Upkeep below), no tools:

- **`pre_llm_call`** (context injection):
  - *First turn of a session*: injects a short reminder that a wiki exists at
    `$WIKI_PATH`, and to orient via `index.md` / `SCHEMA.md` / recent `log.md`
    before external research.
  - *Every turn (first included) with a relevant question*: parses `index.md`
    and scores each entry against the user message:
    - Hyphenated wiki slugs are split (`pool-build-quote` also matches
      "pool", "build", "quote"), so hyphens no longer hide matches.
    - Prefix-tolerant comparison catches inflected forms (pump/pumps,
      cheap/cheaper) and loose words match inside compounds
      (pump in "sandfilterpump").
    - At least two specific terms must overlap. A single shared word, even a
      rare one, is a coincidence: measured on real sessions such lines were
      read 1 time in 108.
    - Image descriptions that Hermes prepends to a message
      (`[The user attached an image. ...]`) are not matched; only the words
      the user typed count.
    - Injects up to 3 matching page lines (truncated to 220 chars each);
      trivial questions ("capital of France") inject nothing.
  - *First turn of a cron session* (session id starts with `cron_`, or
    platform is `cron`): injects a one-time instruction to finish the task
    first and then record durable facts/decisions in the wiki via the
    llm-wiki skill (no personal data, secrets or run logs; silent when
    there is nothing worth saving). This keeps the behaviour central, so
    individual cron prompts need no wiki lines.
- **`post_tool_call`** (observer): counts research tool calls
  (`web_search`, `web_extract`, `session_search`; a `browser_exec` step counts
  for a quarter, since most browser calls test a page rather than research)
  per session. Every N calls (default 3, configurable) arms a one-shot nudge in the next turn: "evaluate NOW
  whether durable findings should be saved to the wiki, following the
  llm-wiki skill". Max 3 nudges per session, so it never becomes noise.

The plugin **never writes to the wiki**. It only injects context; the agent
does the actual filing through its normal wiki workflow (raw source + sha256,
entity/concept page, index.md + log.md updates).

## Upkeep: wiki and memory files

The hooks keep the wiki in use; they do not keep it tidy. Over time index lines go stale, links
break, pages grow past what anyone reads, and Hermes' always-loaded `MEMORY.md` / `USER.md` fill
up until new memories are refused. Two read-only pieces help:

- `maintenance.py`: an audit of the vault (index vs pages, broken wikilinks, frontmatter, tags
  outside the `SCHEMA.md` taxonomy, page size, `log.md` rotation, `raw/` sha256 drift, orphans)
  and of the memory files of every profile (size against the limits in each profile's
  `config.yaml`, counted the way Hermes counts them, long entries, near-duplicates, entries naming
  paths that no longer exist, `SOUL.md` size). `--json` for the data, `--attention` for a short
  stable list (good for change detection).
- `/wiki-audit` (slash command): the same report inside a session, for this profile's memory
  files; `/wiki-audit --all` adds the other profiles (sizes only, no entry text).
- `memory_edit.py`: the only way the upkeep run edits `MEMORY.md` / `USER.md`. It takes Hermes'
  own file lock, matches entries by exact text (entries a live session adds meanwhile survive),
  keeps the 4 newest backups and writes atomically.

The audit and the command never write. Repairs are done by a scheduled agent run that follows
[MAINTENANCE.md](MAINTENANCE.md): backup first, move memory detail into wiki pages and leave a
pointer, fix the index and links, rotate the log, a limit of 15 pages per run, then re-audit and
log what changed. MAINTENANCE.md also shows how to schedule it as a weekly Hermes cron job.

## Install and update

```bash
hermes plugins install seppegadeyne/hermes-wiki-hint --enable
hermes plugins check-updates      # is there newer code on GitHub?
hermes plugins update wiki-hint   # pull it
```

Already have a self-cloned copy in `~/.hermes/plugins/wiki-hint`? Run
`hermes plugins adopt wiki-hint` to make it a tracked git install.

Hooks load on a new session / gateway restart, so restart any running gateway
after install or update. Validate with
`hermes plugins doctor ~/.hermes/plugins/wiki-hint --ci`.

## Configuration

Everything is configured through environment variables (for example in
`~/.hermes/.env`, or a profile's `.env`); the same code runs on every install.

- `WIKI_PATH`: wiki root (an existing directory; `~` and quotes are handled).
  Default: `~/wiki`.
- `WIKI_HINT_LANG`: language of the injected texts, `en` (default) or `nl`.
- `WIKI_HINT_NUDGE_THRESHOLD`: research calls between save nudges
  (default `3`).
- `WIKI_HINT_SKIP_SOURCES`: comma-separated session sources that get no hints
  (default `subagent,tool`). Delegated subagents and scripted
  `hermes chat --source tool` runs never read or save wiki pages, so a hint
  there only costs tokens. Set it to an empty value to hint everywhere.

Messages the harness writes itself (a finished background process, a context
compaction summary, the cron job wrapper; they start with `[IMPORTANT`,
`[SYSTEM` or `[CONTEXT COMPACTION`) get no page suggestions. The first-turn
reminder and the cron instruction still apply.

Further tunables are constants at the top of `__init__.py`:
`MAX_NUDGES_PER_SESSION`, `MAX_INJECT_MATCHES`, `MAX_LINE_CHARS`,
`RESEARCH_TOOLS` (tool weights), `MIN_OVERLAP`, `COMMON_DF_FRACTION` / `COMMON_DF_MIN` (terms
that are very frequent in the index count towards the score but never carry a
match alone), `MIN_WORD_LEN`, `FUZZY_PREFIX`, `MIN_SUBSTR_LEN`,
`MAX_TRACKED_SESSIONS`. A match additionally requires at least one specific
term in the page slug itself, not only in the index summary. A page line shown
once in a session is not injected again (Hermes replays earlier injections).

Injected context is appended to the user message (not the system prompt), so
prompt caching stays intact, and Hermes spills anything over 10k chars to a
file — this plugin stays far below that.

## Requirements

- Hermes Agent with the plugin system (`~/.hermes/plugins/` discovery).
- Python stdlib only.

## License

MIT