# AGENTS.md - wiki-hint

Guidelines for anyone (human or AI agent) modifying this plugin.

## One codebase, no private copy

This repo is the single source of truth. Every install (workstation, VPS
profiles, other machines) runs exactly this code as a git install under
`~/.hermes/plugins/wiki-hint`. Install-specific behaviour lives in
environment variables, never in edits to `__init__.py`:

- `WIKI_PATH`: wiki root (default `~/wiki`).
- `WIKI_HINT_LANG`: `en` (default) or `nl`; texts live in `MESSAGES`.
- `WIKI_HINT_NUDGE_THRESHOLD`: research calls between nudges (default 3).
- `WIKI_HINT_SKIP_SOURCES`: session sources without hints (default
  `subagent,tool`; matched against the hook's `platform` and
  `HERMES_SESSION_SOURCE`).

Rules:
- No private paths, hostnames or names in code, comments, tests or docs.
  Docstrings and comments are English. Add new user-facing text to every
  language in `MESSAGES`.
- Tests (`python3 test_wiki_hint.py` and `python3 test_maintenance.py`,
  stdlib, offline, synthetic wiki and Hermes homes) must pass; assert on
  markers/slugs, not on the exact injected wording.
- Validate with `hermes plugins doctor <plugin-dir> --ci`.
- Bump `version` in `plugin.yaml` on every behaviour change.

## Release flow

1. Edit, run tests + doctor, commit, push to `main`.
2. Every install updates with `hermes plugins check-updates` and
   `hermes plugins update wiki-hint`, then restarts its gateway (or starts a
   new CLI session) so the hooks reload.
3. Before pushing, grep the diff for private paths/names (`/home/`, host and
   person names); the only allowed hit is the author line.

## Behavior that must stay

- The plugin NEVER writes to the wiki itself; context injection via
  `pre_llm_call` and observation via `post_tool_call` only. `maintenance.py`
  and `/wiki-audit` only read; `test_nothing_is_written` guards that.
  `memory_edit.py` is the upkeep run's write path for memory files (flock on
  `<file>.lock` like Hermes, exact-text matching, backups); hooks never import it. Repairs
  belong to the scheduled run described in MAINTENANCE.md, not to plugin code.
- Injection stays short: max 3 index lines, each truncated to 220 chars.
- Save nudge: every N research calls (default 3; weighted, see
  RESEARCH_TOOLS), max 3 per session.
- Tunables are constants at the top of `__init__.py`; per-install settings
  are the env vars above.

## Matching (since 1.1.0, sharpened in 1.2.1)

- `_tokenize` splits hyphenated slugs (`pool-build-quote` -> pool, build,
  quote, and the whole) and drops stopwords/short words.
- `_fuzzy_eq`: equal, prefix-tolerant (pump/pumps), or substring containment
  (pump in sandfilterpump).
- `_best_matches`: terms in > COMMON_DF_FRACTION (12%, min COMMON_DF_MIN=15
  pages) of the index are generic: they score but never carry a match;
  at least one specific term must be in the slug; threshold 2 specific terms
  (the 1-rare-term path was removed in 1.5.0: read 1/108). A hyphenated query word
  does not double-count with its parts. Duplicate index lines are removed.
  Deterministic order via a sorted match-key tiebreaker.
- A page line already shown in a session is not shown again (Hermes replays
  earlier injections verbatim, so repeats only cost tokens).
- Per-session state is created lazily and bounded (MAX_TRACKED_SESSIONS, LRU).
- `WIKI_PATH` expands `~` and strips quotes; invalid UTF-8 in index.md does
  not break the hint.
- Multimodal user messages (list of parts) are flattened to text.
- Page matches fire on EVERY turn (first included) with a relevant question;
  trivial questions stay injection-free.

## Quiet sources and system notes (since 1.4.0)

- Measured on real sessions: subagents and `--source tool` runs read 0 of the
  pages they were shown and never saved to the wiki, while they took ~35% of
  the injected text. They are skipped by default.
- Harness-written messages (`[IMPORTANT`, `[SYSTEM`, `[CONTEXT COMPACTION`)
  get no page matches: their wording is not a user question.

## Image descriptions and browser weight (since 1.5.0)

- `[The user attached/sent an image ...]` blocks (vision descriptions written
  by cli/gateway) are stripped before matching; the user's own text matches.
- Save-nudge counting is weighted (`RESEARCH_TOOLS`): browser_exec = 0.25,
  because browser steps are mostly page QA, not research.
- Measure before tuning: replay real sessions (pages shown vs read later,
  nudges armed) against the old and the new code; keep a change only when it
  drops noise without losing lines that were actually read.

## Cron mode (since 1.2.0)

- `_is_cron` recognises cron sessions by the `cron_` session-id prefix or
  `platform == "cron"`; the instruction is injected on the first turn only.
- Keep it short and silent-by-default: finish the task first, only record
  durable facts, never personal data, secrets or run logs.
- Do not add wiki lines to individual cron prompts; this hook is the single
  place for that behaviour.

## Upkeep audit (since 1.6.0)

- `maintenance.py` is stdlib-only and read-only. Memory size is counted like
  Hermes (`tools/memory_tool_store.py`: entries split on `\n§\n`, stripped,
  re-joined); limits come from each home's `config.yaml` `memory:` block.
- raw/ hashes: the body after the closing `---` line, accepted as stored or
  without its leading blank lines (both forms exist in real vaults).
- Code spans and fences are ignored for wikilinks (schema examples are not links).
- Settings are env vars (`WIKI_HINT_INDEX_LINE_MAX`, `WIKI_HINT_PAGE_MAX_LINES`,
  `WIKI_HINT_LOG_MAX_ENTRIES`, `WIKI_HINT_LOG_ROTATION`, `WIKI_HINT_ARCHIVE_GLOB`,
  `WIKI_HINT_MAINT_HOMES`), never code edits per install.
