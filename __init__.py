"""wiki-hint - Hermes plugin.

Two hooks:
- pre_llm_call: injects a short wiki reminder plus the most relevant wiki pages
  (keyword overlap with the user message), and a one-off "save this" nudge
  after a lot of research.
- post_tool_call: counts research tool calls per session and arms the nudge.

The plugin NEVER writes to the wiki itself; it only nudges the model towards
the llm-wiki skill (orient first, raw/ + sha256, update index/log). The
/wiki-audit command and maintenance.py only read (see MAINTENANCE.md for the
scheduled upkeep that acts on their report).

One codebase for every install. Behaviour is configured via environment
variables (e.g. in ~/.hermes/.env), never by editing this file:
- WIKI_PATH                   wiki root (default: ~/wiki)
- WIKI_HINT_LANG              language of injected texts: "en" (default) or "nl"
- WIKI_HINT_NUDGE_THRESHOLD   research calls between save nudges (default: 3)
- WIKI_HINT_SKIP_SOURCES      comma-separated session sources that get no hints
                              (default: "subagent,tool")

Quiet where a hint cannot help: delegated subagents (platform "subagent") and
scripted runs started with `hermes chat --source tool` get nothing, because they
neither read nor save wiki pages. Messages the harness writes itself (a finished
background process, a context-compaction summary, the cron job wrapper) get no
page suggestions; the first-turn reminder and the cron instruction still apply.

Matching v2: tokens are split on hyphens (wiki slugs such as
"pool-build-quote" count as the separate words pool/build/quote) and compared
prefix-tolerantly, so inflected forms (pump/pumps) match. Loose words also match
inside compounds (pump in sandfilterpump). Generic terms (very frequent in the
index) add to the score but never carry a match alone; at least one specific
term must appear in the page slug, and at least MIN_OVERLAP specific terms
must overlap (one shared word is a coincidence, not a topic). Multimodal user
messages (list of parts) are flattened instead of ignored; image descriptions
that Hermes prepends to the user's text are not matched.

The save nudge counts research tools by weight: a browser step counts for a
quarter, since most browser calls are page tests rather than research.
"""

import json
import os
import re
import threading

WIKI_FALLBACK = os.path.expanduser("~/wiki")
# Research tools and their weight towards the save nudge. One browser step is usually one of
# many clicks while testing a page (QA, not research), so it counts for a quarter.
RESEARCH_TOOLS = {"web_search": 1.0, "web_extract": 1.0, "session_search": 1.0, "browser_exec": 0.25}
DEFAULT_NUDGE_THRESHOLD = 3  # research calls before the save nudge
MAX_NUDGES_PER_SESSION = 3
CRON_SESSION_PREFIX = "cron_"  # Hermes cron sessions are named cron_<jobid>_<date>
MAX_INJECT_MATCHES = 3
MAX_LINE_CHARS = 220         # truncation length per injected index line
MIN_WORD_LEN = 3             # shorter (sub)words are noise
MIN_OVERLAP = 2              # content terms that must overlap
FUZZY_PREFIX = 5             # common prefix length for fuzzy matching
MIN_SUBSTR_LEN = 6           # min length for substring matching
COMMON_DF_FRACTION = 0.12    # word in > this share of the index = generic
COMMON_DF_MIN = 15           # ... but never below this page count (small wikis)
MAX_TRACKED_SESSIONS = 256   # per-session state is bounded (oldest dropped)
DEFAULT_LANG = "en"
DEFAULT_SKIP_SOURCES = "subagent,tool"
# Messages the harness writes itself start with one of these tags. Matching their wording
# against the index only produces noise; a real user message never starts this way.
SYSTEM_NOTE_RE = re.compile(r"^\s*\[(?:IMPORTANT|SYSTEM|CONTEXT COMPACTION)\b")
# Hermes prepends a vision model's description of an attached image to the user's text. That
# description is long and generic ("analysis", "provided", "page") and matched random pages;
# only the user's own words are matched. Full block first (the description may contain "]"),
# then failure notes, which can carry multi-line exception text but always end in "image_url: ...]".
# If Hermes changes the format, nothing is stripped (fails open, as before 1.5.0).
ATTACHMENT_BLOCK_RE = re.compile(
    r"\[The user (?:attached|sent) an image.*?\]\s*\[(?:If you need a closer look|Examine it)[^\]]*\]",
    re.S)
ATTACHMENT_NOTE_RE = re.compile(r"\[The user (?:attached|sent) an image.*?image_url:[^\]\n]*\]", re.S)

MESSAGES = {
    "en": {
        "nudge": (
            "[wiki-hint] You have done a lot of research in the past minutes "
            "({n}+ research calls). Evaluate NOW whether there are durable "
            "findings (new facts, decisions, working recipes, sources worth "
            "keeping). If so: save them proactively in the wiki at {wiki} "
            "following the llm-wiki skill (raw source with sha256, "
            "entity/concept page, update index.md + log.md). If not: carry on "
            "without mentioning this."
        ),
        "first_turn": (
            "[wiki-hint] The user keeps an llm-wiki (knowledge base) at {wiki}. "
            "Use it for research: orient via index.md, SCHEMA.md and the recent "
            "log.md before starting external research or creating new notes. "
            "Save durable findings proactively to the wiki (llm-wiki skill), "
            "not only in your answer."
        ),
        "cron": (
            "[wiki-hint] This is a scheduled (cron) run. An llm-wiki lives at {wiki}. "
            "Finish your task COMPLETELY first. Then briefly assess whether there are "
            "durable facts or decisions a later run or another agent needs (confirmed "
            "findings, discovered pitfalls, working recipes, decisions with reasons). "
            "If so: record them concisely following the llm-wiki skill (orient via "
            "SCHEMA.md/index.md, update the right concept/entity/query page or create "
            "one, update index.md and log.md; no duplicate pages). Never personal "
            "data, secrets, tokens or raw tool output; no run log or status that "
            "already lives in GitHub/Fizzy. If not: write nothing and do not mention it."
        ),
        "matches": (
            "[wiki-hint] Possibly relevant wiki pages at {wiki} "
            "(read these before external research):"
        ),
    },
    "nl": {
        "nudge": (
            "[wiki-hint] Je hebt de voorbije minuten veel opzoekwerk gedaan "
            "({n}+ research-calls). Evalueer NU of er duurzame "
            "bevindingen zijn (nieuwe feiten, beslissingen, werkende recepten, "
            "bronnen om te bewaren). Zo ja: bewaar ze proactief in de wiki onder "
            "{wiki} volgens de llm-wiki skill (raw-bron met sha256, "
            "entity/concept-pagina, index.md + log.md bijwerken). Zo nee: ga "
            "verder zonder hierover te melden."
        ),
        "first_turn": (
            "[wiki-hint] Er is een llm-wiki (kennisbank) onder {wiki}. "
            "Gebruik die bij opzoekwerk: oriënteer eerst via index.md, SCHEMA.md "
            "en recent log.md voordat je extern onderzoek start of nieuwe "
            "notities maakt. Duurzame bevindingen schrijf je proactief weg naar "
            "de wiki (llm-wiki skill), niet alleen in het antwoord."
        ),
        "cron": (
            "[wiki-hint] Dit is een geplande (cron) run. Er staat een llm-wiki onder {wiki}. "
            "Rond je taak EERST volledig af. Beoordeel daarna kort of er duurzame feiten of "
            "beslissingen zijn die een volgende run of een andere agent nodig heeft (bevestigde "
            "bevindingen, ontdekte valkuilen, werkende recepten, gemaakte keuzes met reden). "
            "Zo ja: leg ze beknopt vast volgens de llm-wiki skill (oriënteer via SCHEMA.md/index.md, "
            "werk de juiste concept/entity/query-pagina bij of maak er één, werk index.md en log.md bij; "
            "geen dubbele pagina's). Nooit persoonsgegevens, secrets, tokens of ruwe tool-output; "
            "geen run-logboek of status die al in GitHub/Fizzy staat. Zo nee: schrijf niets en meld hier niets over."
        ),
        "matches": (
            "[wiki-hint] Mogelijk relevante wiki-pagina's onder {wiki} "
            "(lees deze vóór extern opzoekwerk):"
        ),
    },
}


def _lang():
    lang = os.environ.get("WIKI_HINT_LANG", "").strip().strip("\"'").strip().lower()
    return lang if lang in MESSAGES else DEFAULT_LANG


def _msg(key, **kw):
    return MESSAGES[_lang()][key].format(**kw)


def _skip_sources():
    raw = os.environ.get("WIKI_HINT_SKIP_SOURCES")
    if raw is None:
        raw = DEFAULT_SKIP_SOURCES
    return {s.strip().lower() for s in raw.strip().strip("\"'").split(",") if s.strip()}


def _skipped(platform):
    """True for a session whose source gets no hints (platform, or the --source of a one-shot run)."""
    skip = _skip_sources()
    if not skip:
        return False
    sources = {str(platform or "").strip().lower(), os.environ.get("HERMES_SESSION_SOURCE", "").strip().lower()}
    return bool(sources & skip)


def _is_system_note(text):
    return bool(SYSTEM_NOTE_RE.match(text or ""))


def _strip_attachments(text):
    """Drop harness-written image descriptions, keep what the user typed."""
    text = ATTACHMENT_BLOCK_RE.sub(" ", text or "")
    return ATTACHMENT_NOTE_RE.sub(" ", text)


def _nudge_threshold():
    """Read per call so a changed env takes effect without code edits."""
    raw = os.environ.get("WIKI_HINT_NUDGE_THRESHOLD", "").strip().strip("\"'").strip()
    try:
        n = int(raw)
    except ValueError:
        return DEFAULT_NUDGE_THRESHOLD
    return n if n >= 1 else DEFAULT_NUDGE_THRESHOLD


STOPWORDS = {
    # dutch
    "de", "het", "een", "van", "dat", "die", "met", "voor", "aan", "over",
    "naar", "op", "in", "uit", "bij", "als", "is", "ben", "zijn", "was", "were",
    "heb", "hebt", "hebben", "had", "would", "zou", "kun", "kan", "kunnen",
    "wordt", "worden", "werd", "maar", "ook", "nog", "al", "net", "wel", "niet",
    "wat", "hoe", "waar", "wanneer", "waarom", "wie", "mijn", "jouw", "zijn",
    "ons", "jullie", "daar", "hier", "daarna", "toen", "even", "gewoon", "zelf",
    "meest", "veel", "meer", "goed", "nieuw", "nieuwe", "blijf", "graag",
    "welke", "welk", "alle", "alles", "geen", "welke", "doen", "doe", "done",
    "ga", "gaan", "maak", "maken", "zoek", "zoeken", "vind", "vinden",
    "want", "omdat", "dus", "toch", "echt", "heel", "erg", "twee", "drie",
    # english
    "the", "and", "for", "that", "this", "with", "from", "have", "has", "was",
    "were", "would", "could", "should", "about", "into", "your", "you", "are",
    "its", "not", "but", "can", "will", "just", "like", "what", "how", "where",
    "when", "why", "who", "them", "they", "there", "then", "than", "out",
    "which", "all", "any", "get", "got", "does", "did", "some", "such",
}

_lock = threading.Lock()
_index_cache = {"path": None, "mtime": None, "entries": []}
_session_state = {}  # session_id -> {"count", "nudges", "pending", "shown"}


def _wiki_path():
    # expand ~ and strip literal quotes around a path with spaces
    p = os.environ.get("WIKI_PATH", "").strip().strip("\"'").strip()
    p = os.path.expanduser(p) if p else ""
    if p and os.path.isdir(p):
        return p
    if os.path.isdir(WIKI_FALLBACK):
        return WIKI_FALLBACK
    return None


def _flatten_message(message):
    """pre_llm_call may receive a multimodal list (text/image parts);
    extract the text parts so turns with photos also match."""
    if isinstance(message, str):
        return message
    if isinstance(message, list):
        parts = []
        for part in message:
            if isinstance(part, dict):
                text = part.get("text") or ""
                if isinstance(text, str):
                    parts.append(text)
            elif isinstance(part, str):
                parts.append(part)
        return " ".join(parts)
    return ""


def _tokenize(text):
    """Drop short (sub)words and stopwords; hyphenated slugs are split into
    their parts AND kept whole, so "fan-curves" matches "fan-curves", "fan"
    and "curves"."""
    text = _flatten_message(text)
    words = re.findall(r"[a-z0-9][a-z0-9\-]*", text.lower())
    tokens = set()
    for w in words:
        if len(w) >= MIN_WORD_LEN and w not in STOPWORDS:
            tokens.add(w)
        for part in w.split("-"):
            if len(part) >= MIN_WORD_LEN and part not in STOPWORDS:
                tokens.add(part)
    return tokens


def _fuzzy_eq(w, v):
    """Equal, common prefix >= FUZZY_PREFIX, the shortest is a prefix of the
    longest (pump/pumps), or the shortest is a substring of the longest
    (pump in sandfilterpump)."""
    if w == v:
        return True
    if len(w) < MIN_WORD_LEN or len(v) < MIN_WORD_LEN:
        return False
    if w in v or v in w:
        # do not let fragments introduced purely by the hyphen split match
        # loose fragments of another hyphenated slug
        return len(w) >= MIN_SUBSTR_LEN and len(v) >= MIN_SUBSTR_LEN
    p = os.path.commonprefix([w, v])
    if p and (p == w or p == v):
        return True
    m = min(len(w), len(v))
    return len(p) >= FUZZY_PREFIX and len(p) >= m - 2


def _overlap(a, b):
    """Number of terms in a with at least one (fuzzy) match in b."""
    count = 0
    for w in a:
        for v in b:
            if _fuzzy_eq(w, v):
                count += 1
                break
    return count


def _index_entries(wiki):
    """Parse index.md into (title, tokens, line) tuples; cached on mtime."""
    idx = os.path.join(wiki, "index.md")
    try:
        mtime = os.path.getmtime(idx)
    except OSError:
        return []
    with _lock:
        if _index_cache["path"] == idx and _index_cache["mtime"] == mtime:
            return _index_cache["entries"]
        try:
            # errors="replace": one broken byte must not break the whole hint
            with open(idx, encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except (OSError, ValueError):
            return []
        entries = []
        for line in lines:
            m = re.match(r"\s*-\s*\[\[([^\]|#]+)", line)
            if not m:
                continue
            slug = m.group(1).strip()
            title = slug.rsplit("/", 1)[-1].replace("-", " ")
            # index summaries can be very long; the injection stays short
            short = line.strip()[:MAX_LINE_CHARS]
            entries.append((title, _tokenize(slug), _tokenize(line), short))
        _index_cache["path"] = idx
        _index_cache["mtime"] = mtime
        _index_cache["entries"] = entries
        return entries


def _best_matches(user_message, entries):
    """Top index lines (unique, max MAX_INJECT_MATCHES).

    - Generic terms (in > COMMON_DF_FRACTION of the index) count towards the
      score but cannot carry a match.
    - At least one specific term must be in the page slug itself: a hit only
      in the summary is too weak.
    - Specific overlap >= MIN_OVERLAP. A single shared word, however rare, is
      not enough: measured on real sessions such lines were almost never read.
      (A hyphenated compound such as "stop-loss" counts as its parts.)
    - A compound query word ("self-host") does not double-count next to its
      own parts ("self", "host")."""
    q = _tokenize(user_message or "")
    if not q:
        return []
    hit_lists = []  # (matched query words, slug_tokens, line)
    for title, slug_tokens, tokens, line in entries:
        matched = {w for w in q if any(_fuzzy_eq(w, v) for v in tokens)}
        matched = tuple(sorted(
            w for w in matched
            if "-" not in w or not any(p in matched for p in w.split("-"))
        ))
        if matched:
            hit_lists.append((matched, slug_tokens, line))
    if not hit_lists:
        return []
    df = {}
    for matched, _, _ in hit_lists:
        for w in matched:
            df[w] = df.get(w, 0) + 1
    common_df = max(COMMON_DF_MIN, int(len(entries) * COMMON_DF_FRACTION))
    scored = []
    for matched, slug_tokens, line in hit_lists:
        specific = [w for w in matched if df[w] <= common_df]
        if len(specific) < MIN_OVERLAP:
            continue
        if not any(_fuzzy_eq(w, v) for w in specific for v in slug_tokens):
            continue
        scored.append((len(matched), " ".join(matched), line))
    scored.sort(key=lambda x: (-x[0], x[1]))
    out = []
    for _, _, line in scored:
        if line not in out:  # index.md may contain the same line twice
            out.append(line)
            if len(out) >= MAX_INJECT_MATCHES:
                break
    return out


def _state(session_id, create=True):
    """Per-session state; only created when there is something to remember
    (research calls or shown pages) and bounded at MAX_TRACKED_SESSIONS (least
    recently used is dropped). Call with _lock held."""
    key = session_id or "default"
    st = _session_state.pop(key, None)
    if st is None:
        if not create:
            return None
        st = {"count": 0, "nudges": 0, "pending": False, "shown": set()}
        while len(_session_state) >= MAX_TRACKED_SESSIONS:
            _session_state.pop(next(iter(_session_state)))
    _session_state[key] = st  # last = most recently used
    return st


def _post_tool_call(tool_name, args=None, session_id=None, **kwargs):
    weight = RESEARCH_TOOLS.get(tool_name)
    if not weight:
        return
    threshold = _nudge_threshold()
    with _lock:
        st = _state(session_id)
        before = int(st["count"] // threshold)
        st["count"] += weight
        if int(st["count"] // threshold) > before and st["nudges"] < MAX_NUDGES_PER_SESSION:
            st["pending"] = True


def _is_cron(session_id, platform=None):
    return str(session_id or "").startswith(CRON_SESSION_PREFIX) or str(platform or "") == "cron"


def _cron_instruction(wiki):
    return _msg("cron", wiki=wiki)


def _pre_llm_call(user_message, is_first_turn=False, session_id=None, **kwargs):
    if _skipped(kwargs.get("platform")):
        return None
    wiki = _wiki_path()
    if not wiki:
        return None

    parts = []
    with _lock:
        st = _state(session_id, create=False)
        pending = bool(st and st["pending"])
        if pending:
            st["pending"] = False
            st["nudges"] += 1

    if pending:
        parts.append(_msg("nudge", wiki=wiki, n=_nudge_threshold()))

    if is_first_turn:
        parts.append(_msg("first_turn", wiki=wiki))

    if is_first_turn and _is_cron(session_id, kwargs.get("platform")):
        parts.append(_cron_instruction(wiki))

    # Page suggestions on every turn (first included) with a relevant question.
    # Hermes replays earlier injections verbatim in the history (api_content),
    # so lines already shown this session are left out. A failure here must not
    # take the reminder / cron instruction down with it.
    try:
        text = _flatten_message(user_message)
        if _is_system_note(text):
            matches = []
        else:
            matches = _best_matches(_strip_attachments(text), _index_entries(wiki))
    except Exception:
        matches = []
    if matches:
        with _lock:
            st = _state(session_id)
            matches = [m for m in matches if m not in st["shown"]]
            st["shown"].update(matches)
    if matches:
        listing = "\n".join(f"  {m}" for m in matches)
        parts.append(_msg("matches", wiki=wiki) + "\n" + listing)

    if not parts:
        return None
    return {"context": "\n\n".join(parts)}


def _maintenance():
    """The read-only audit module next to this file (works as a package and when loaded by path)."""
    try:
        from . import maintenance  # type: ignore[attr-defined]
        return maintenance
    except ImportError:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "wiki_hint_maintenance", os.path.join(os.path.dirname(os.path.abspath(__file__)), "maintenance.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


def _audit_command(raw_args="", **_kwargs):
    """/wiki-audit: read-only health report of the wiki and this profile's memory files.

    ``--all`` adds the other profiles of the install (sizes only, no entry text: the command may run
    in a chat other people see); ``--json`` returns the raw data for this profile.
    """
    try:
        m = _maintenance()
        args = str(raw_args or "").split()
        homes = m.hermes_homes() if "--all" in args else [m.active_home()]
        report = m.audit(_wiki_path(), homes)
        if "--json" in args and "--all" not in args:
            return json.dumps(report, ensure_ascii=False, indent=1)[:60_000]
        return m.render_text(report, entry_text="--all" not in args)
    except Exception as exc:  # a slash command must always answer
        return f"wiki-hint audit failed: {str(exc)[:300]}"


def register(ctx):
    ctx.register_hook("pre_llm_call", _pre_llm_call)
    ctx.register_hook("post_tool_call", _post_tool_call)
    if hasattr(ctx, "register_command"):
        ctx.register_command("wiki-audit", handler=_audit_command,
                             description="Read-only health report of the wiki and the memory files")
    if hasattr(ctx, "register_cli_command"):
        ctx.register_cli_command(name="wiki-hint", help="wiki-hint tools (upkeep cron job)",
                                 setup_fn=_cli_setup, handler_fn=_cli_handle,
                                 description="Set up the weekly wiki and memory upkeep: hermes wiki-hint upkeep install")


def _upkeep():
    """upkeep.py next to this file (package import or loaded by path), only for the CLI command."""
    try:
        from . import upkeep  # type: ignore[attr-defined]
        return upkeep
    except ImportError:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "wiki_hint_upkeep", os.path.join(os.path.dirname(os.path.abspath(__file__)), "upkeep.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


def _cli_setup(parser):
    _upkeep().setup_parser(parser)


def _cli_handle(args):
    return _upkeep().handle(args)
