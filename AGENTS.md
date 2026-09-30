# AGENTS.md — wiki-hint

Guidelines for anyone (human or AI agent) modifying this plugin.

## Dual copy: private install + public repo

This plugin exists as two deliberately different copies:

1. **Private install** (the owner's machine): Dutch injected strings and a
   hardcoded fallback wiki path.
2. **Public repo** (this one): sanitized English copy —
   `WIKI_FALLBACK = os.path.expanduser("~/wiki")`, no private paths or
   names, English docstrings/comments.

**Workflow for changes:**
- Always edit the **private** copy first (with its localized strings and
  fixed wiki path) and test there.
- Sync only the **logic** to the public copy — the two copies MAY differ
  textually (language, fallback path, author). Never sync strings over.
- Validate the private copy with
  `hermes plugins doctor <plugin-dir> --ci`.

**Publication flow to this repo:**
1. Copy to a fresh `/tmp` directory (never reuse a stale one).
2. Strip private paths and names; fallback → `~/wiki`; docstrings/comments
   to English.
3. Smoke-test via importlib against a real wiki (first-turn hint, index
   match, nudge threshold).
4. Push, then make a fresh public clone and grep for private paths/names —
   the only allowed hit is the author line.

## Behavior that must stay

- The plugin NEVER writes to the wiki itself; context injection via
  `pre_llm_call` and observation via `post_tool_call` only.
- Injection stays short: max 3 index lines, each truncated to 220 chars.
- Save nudge: every 5 research calls, max 3 per session.
- All thresholds are constants at the top of `__init__.py`.

## Matching v2 (since 1.1.0)

- `_tokenize` splits hyphenated slugs (`pool-build-quote` -> pool, build,
  quote, and the whole) and drops stopwords/short words.
- `_fuzzy_eq`: equal, prefix-tolerant (pump/pumps), or substring containment
  (pump in sandfilterpump).
- `_best_matches`: threshold of 2 overlapping terms, OR 1 rare term
  (document frequency <= RARE_DF_MAX across index.md). Deterministic order
  via a sorted match-key tiebreaker.
- Multimodal user_messages (list-of-parts) are flattened to text.
- Page matches fire on EVERY turn (first included) with a relevant question;
  trivial questions stay injection-free.