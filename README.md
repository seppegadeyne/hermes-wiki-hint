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

Two lifecycle hooks, no tools:

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
    - One overlapping term suffices when it is rare across the index
      (document frequency <= 3 pages), so "the charger acts weird" finds
      the charger page without generic words injecting noise.
    - Injects up to 3 matching page lines (truncated to 220 chars each);
    trivial questions ("capital of France") inject nothing.
- **`post_tool_call`** (observer): counts research tool calls
  (`web_search`, `web_extract`, `browser_exec`, `session_search`) per session.
  Every 3rd call arms a one-shot nudge in the next turn: "evaluate NOW
  whether durable findings should be saved to the wiki, following the
  llm-wiki skill". Max 3 nudges per session, so it never becomes noise.

The plugin **never writes to the wiki**. It only injects context; the agent
does the actual filing through its normal wiki workflow (raw source + sha256,
entity/concept page, index.md + log.md updates).

## Install

```bash
git clone https://github.com/seppegadeyne/hermes-wiki-hint.git
mkdir -p ~/.hermes/plugins/wiki-hint
cp hermes-wiki-hint/plugin.yaml hermes-wiki-hint/__init__.py ~/.hermes/plugins/wiki-hint/
```

Then enable it in `~/.hermes/config.yaml` (per profile too, if you use
profiles):

```yaml
plugins:
  enabled:
    - wiki-hint
```

Validate with the built-in plugin doctor:

```bash
hermes plugins doctor ~/.hermes/plugins/wiki-hint --ci
```

Restart any running gateway so the hooks load.

## Configuration

- Wiki root resolution: `$WIKI_PATH` (if set and an existing directory),
  otherwise `~/wiki`. Edit `WIKI_FALLBACK` in `__init__.py` to change it.
- Tunables at the top of `__init__.py`:
  - `NUDGE_THRESHOLD = 3` — research calls between save nudges
  - `MAX_NUDGES_PER_SESSION = 3`
  - `MAX_INJECT_MATCHES = 3` — max wiki pages injected per turn
  - `MAX_LINE_CHARS = 220` — injected index-line truncation
  - `MIN_OVERLAP = 2` / `RARE_DF_MAX = 3` — page match threshold, and the
    document frequency under which a single rare term counts as a match
  - `MIN_WORD_LEN`, `FUZZY_PREFIX`, `MIN_SUBSTR_LEN` — tokenizer/matcher
    knobs (hyphen splitting, prefix tolerance, substring containment). A
    single-term match additionally requires the term to appear in the page
    slug itself, not only in the index summary.

Injected context is appended to the user message (not the system prompt), so
prompt caching stays intact, and Hermes spills anything over 10k chars to a
file — this plugin stays far below that.

## Requirements

- Hermes Agent with the plugin system (`~/.hermes/plugins/` discovery).
- Python stdlib only.

## License

MIT