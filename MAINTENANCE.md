# Scheduled wiki and memory upkeep

The plugin itself never writes. Upkeep is a scheduled agent run (a Hermes cron job) that reads
the audit report and repairs what it lists, following this playbook. Hermes plugins have no
scheduling surface, so the schedule is one cron job per install (see "Schedule it" below).

## What it keeps in order

1. The wiki at `WIKI_PATH`: index, links, frontmatter, tags, page size, `log.md` rotation and
   `raw/` hashes, as reported by `maintenance.py`.
2. Hermes' always-loaded files in every profile of the install: `memories/MEMORY.md` and
   `memories/USER.md`. These are injected into every turn and have hard character limits; a full
   file makes Hermes refuse new memories. The upkeep keeps them under 85% of their limit by moving
   detail into the wiki and leaving a short pointer, and removes stale or duplicate entries.
3. `SOUL.md` is reported only (size); it is the operator's text and is never edited by the run.

## Run order

1. Read the report the job injects (or run `python3 <plugin>/maintenance.py --json`). Stop with a
   one-line "nothing to do" when `attention` is empty.
2. Back up the vault before the first write, to a folder that is not pruned (not a scratch or
   temp folder): `mkdir -p $HERMES_HOME/backups/wiki-upkeep && tar -czf
   $HERMES_HOME/backups/wiki-upkeep/<date>.tgz -C <vault-parent> <vault-dir>`. Keep the 4 newest
   tarballs, delete older ones. Memory files are backed up by `memory_edit.py` itself.
3. Orient in the wiki (llm-wiki skill): `SCHEMA.md`, `index.md`, the last entries of `log.md`.
4. Repair, in this order, within the limits below.
5. Re-run the audit once and compare: every count you worked on must go down, nothing else may
   go up. If something went up, undo: the vault from its tarball, each memory file with
   `memory_edit.py restore <file> <backup>` (never a plain copy: that skips the lock). Re-add, with
   an `apply` plan, any `not_in_backup` entry that is not your own text (a live session wrote it).
   Do not retry; report it. Never run more than one repair pass.
6. Append ONE `log.md` entry (`## [YYYY-MM-DD] lint | upkeep`) listing what changed and what was left.
7. Final response: a short report (what changed, what was left and why, before/after numbers).

## Repairs

Wiki:
- Index line points to a missing page: find the page (renamed/archived) and fix the link; if it is
  gone, remove the line.
- Page missing from the index: add a line in the right section (`- [[path]] — core sentence`,
  within the line limit), unless it is an archive page that a hub links to.
- Index line over the limit: shorten the summary; keep the specific words that identify the page.
- Broken wikilink: point it at the page that exists (search by slug and title). If nothing fits,
  turn it into plain text; never create an empty page to satisfy a link.
- Missing frontmatter fields: add them from the page itself (title from the heading, dates from
  the log or file times, type from the folder).
- Tag outside the taxonomy: map it to an existing tag. Add a tag to `SCHEMA.md` only when no
  existing tag fits and it is used on 2+ pages.
- Page over the line limit: split only pages with dated sections, by moving the oldest dated
  sections verbatim into a statuslog page and linking it from the core page (llm-wiki skill,
  "Optimizing a large vault"). Verify the move with a before/after count of non-empty lines.
  Undated pages: report, do not rewrite.
- `log.md` rotation due: move the old entries verbatim to `_archive/log-YYYY-MM.md` (or the
  vault's own archive naming) and check the entry counts add up.
- `raw/` drift: report only. Raw sources are immutable; a re-hash is the operator's call.

Memory files (per profile, `memories/MEMORY.md` and `memories/USER.md`):
- Edit them only through `python3 <plugin>/memory_edit.py`: `show <file>` lists the entries,
  `apply <file> <plan.json>` applies `{"remove": [...], "replace": [{"old", "new"}], "add": [...],
  "limit": N}` (`limit` required: 85% of the limit in the report) under Hermes' own file lock, with a backup and an atomic write, and matches entries
  by exact text, so an entry a live session adds meanwhile survives. Never write these files with
  write_file, patch or a shell redirect: a live session may be writing at the same moment.
- The operator authorizes the upkeep run to edit `memories/` of every profile in the report;
  nothing else in another profile is touched.
- Target: under 85% of the limit from that profile's `config.yaml` (the report shows it).
- First remove what is no longer true: entries naming paths that no longer exist, decisions that
  a newer entry or the wiki overrides, finished one-off tasks. When unsure whether something is
  stale, keep it.
- Merge near-duplicate entries into one.
- Move detail out: an entry that holds a procedure, a list of hosts/IDs or background goes to the
  matching wiki page (or skill, when it is a how-to for one kind of task); the memory entry keeps
  one line with the fact that must be known every turn and the wiki page name.
- Never drop information without putting it somewhere: every removed fact is either stale (say
  why in the report) or moved (say where).
- A missing path in an entry is a hint to check the rest of the entry (renamed folder? moved
  project?), never on its own a reason to remove it.
- `USER.md` describes the person: shorten and merge there, but never move its content into the
  wiki (personal data stays out of the vault).
- Before writing memory detail into the vault, check the text for secrets and personal data
  (`token`, `api key`, `password`, `secret`, `PRIVATE KEY`, `sk-`, `ghp_`, phone numbers,
  addresses). On a hit: leave the entry where it is and report it.

## Limits per run

- At most 15 wiki pages changed (index.md and log.md not counted). Leave the rest for the next run
  and say so.
- No new content pages, except a statuslog page created by a split or a page that receives memory
  detail and does not exist yet.
- Never delete a content page; archive it (`_archive/`, remove from the index) only when the
  report and the page itself show it is superseded.
- Do not touch other repositories, cron jobs, plugins, configs or `SOUL.md`.

## Schedule it

Create a weekly cron job with:
- `script`: a small wrapper in `$HERMES_HOME/scripts/` that runs
  `python3 <plugin>/maintenance.py --json` with `WIKI_PATH` set, so the report is injected as context;
- `monitor`: the same wrapper with `--attention` (stable text), so a week without changes skips
  the agent run;
- a prompt that says: follow `<plugin>/MAINTENANCE.md`, headless, no questions, report at the end,
  and that the operator authorizes edits to `memories/` in every profile of the report;
- `skills: [llm-wiki]`.

The run needs file writes (vault) and the terminal (memory_edit.py, tar) without a person to
approve them: check the profile's `approvals.cron_mode` before relying on it.

Settings (env, as for the hooks): `WIKI_HINT_INDEX_LINE_MAX`, `WIKI_HINT_PAGE_MAX_LINES`,
`WIKI_HINT_LOG_MAX_ENTRIES`, `WIKI_HINT_LOG_ROTATION` (`entries` or `monthly`),
`WIKI_HINT_ARCHIVE_GLOB` (archive pages by design, e.g. `*-statuslog-*`) and
`WIKI_HINT_MAINT_HOMES` (Hermes homes to check; default: the active home and its profiles).
