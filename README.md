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
  - *Later turns*: parses `index.md`, scores each entry against the user
    message by keyword overlap (prefix-tolerant, so inflected word forms
    still match), and injects up to 3 matching page lines (truncated to 220
    chars each).
- **`post_tool_call`** (observer): counts research tool calls
  (`web_search`, `web_extract`, `browser_exec`, `session_search`) per session.
  Every 5th call arms a one-shot nudge in the next turn: "evaluate NOW
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
  - `NUDGE_THRESHOLD = 5` — research calls between save nudges
  - `MAX_NUDGES_PER_SESSION = 3`
  - `MAX_INJECT_MATCHES = 3` — max wiki pages injected per turn
  - `MAX_LINE_CHARS = 220` — injected index-line truncation

Injected context is appended to the user message (not the system prompt), so
prompt caching stays intact, and Hermes spills anything over 10k chars to a
file — this plugin stays far below that.

## Requirements

- Hermes Agent with the plugin system (`~/.hermes/plugins/` discovery).
- Python stdlib only.

## License

MIT