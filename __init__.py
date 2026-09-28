"""wiki-hint - Hermes plugin.

Two hooks:
- pre_llm_call: injects a short wiki reminder plus the most relevant wiki
  pages (keyword overlap with the user message), plus a one-shot "save this"
  nudge after heavy research.
- post_tool_call: counts research tool calls per session and arms the nudge.

The plugin NEVER writes to the wiki itself; it only nudges the model toward
the llm-wiki skill workflow (orient first, raw source + sha256, update
index.md and log.md).
"""

import os
import re
import threading

WIKI_FALLBACK = os.path.expanduser("~/wiki")
RESEARCH_TOOLS = {"web_search", "web_extract", "browser_exec", "session_search"}
NUDGE_THRESHOLD = 5          # research-calls voordat de bewaringsduw komt
MAX_NUDGES_PER_SESSION = 3
MAX_INJECT_MATCHES = 3

STOPWORDS = {
    # nederlands
    "de", "het", "een", "van", "en", "dat", "die", "met", "voor", "aan", "over",
    "naar", "op", "in", "uit", "bij", "als", "is", "ben", "zijn", "was", "were",
    "heb", "hebt", "hebben", "had", "would", "zou", "kun", "kan", "kunnen",
    "wordt", "worden", "werd", "maar", "ook", "nog", "al", "net", "wel", "niet",
    "wat", "hoe", "waar", "wanneer", "waarom", "wie", "mijn", "jouw", "zijn",
    "ons", "jullie", "daar", "hier", "daarna", "toen", "even", "gewoon", "zelf",
    "meest", "veel", "meer", "goed", "nieuw", "nieuwe", "blijf", "graag",
    # english
    "the", "and", "for", "that", "this", "with", "from", "have", "has", "was",
    "were", "would", "could", "should", "about", "into", "your", "you", "are",
    "its", "not", "but", "can", "will", "just", "like", "what", "how", "where",
    "when", "why", "who", "them", "they", "there", "then", "than", "out",
}

_lock = threading.Lock()
_index_cache = {"path": None, "mtime": None, "entries": []}
_session_state = {}  # session_id -> {"count": int, "nudges": int, "pending": bool}


def _wiki_path():
    p = os.environ.get("WIKI_PATH", "").strip()
    if p and os.path.isdir(p):
        return p
    if os.path.isdir(WIKI_FALLBACK):
        return WIKI_FALLBACK
    return None


def _tokenize(text):
    words = re.findall(r"[a-z0-9][a-z0-9\-]{3,}", text.lower())
    return {w for w in words if w not in STOPWORDS}


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
            with open(idx, encoding="utf-8") as f:
                lines = f.readlines()
        except OSError:
            return []
        entries = []
        for line in lines:
            m = re.match(r"\s*-\s*\[\[([^\]|#]+)", line)
            if not m:
                continue
            slug = m.group(1).strip()
            title = slug.rsplit("/", 1)[-1].replace("-", " ")
            # index summaries can be very long; keep the injection short
            short = line.strip()[:220]
            entries.append((title, _tokenize(line), short))
        _index_cache["path"] = idx
        _index_cache["mtime"] = mtime
        _index_cache["entries"] = entries
        return entries


def _best_matches(user_message, entries):
    q = _tokenize(user_message or "")
    if not q:
        return []
    scored = []
    for title, tokens, line in entries:
        overlap = _overlap(q, tokens)
        if overlap >= 2:
            scored.append((overlap, line))
    scored.sort(key=lambda x: -x[0])
    return [line for _, line in scored[:MAX_INJECT_MATCHES]]


def _overlap(a, b):
    """Count matches, tolerant of inflected word forms: equal tokens OR a
    common prefix of >= 6 characters (e.g. 'betaalbaar'/'betaalbare')."""
    count = 0
    for w in a:
        for v in b:
            if w == v or len(os.path.commonprefix([w, v])) >= 6:
                count += 1
                break
    return count


def _state(session_id):
    with _lock:
        return _session_state.setdefault(
            session_id or "default", {"count": 0, "nudges": 0, "pending": False}
        )


def _post_tool_call(tool_name, args=None, session_id=None, **kwargs):
    if tool_name not in RESEARCH_TOOLS:
        return
    st = _state(session_id)
    with _lock:
        st["count"] += 1
        if (
            st["count"] % NUDGE_THRESHOLD == 0
            and st["nudges"] < MAX_NUDGES_PER_SESSION
        ):
            st["pending"] = True


def _pre_llm_call(user_message, is_first_turn=False, session_id=None, **kwargs):
    wiki = _wiki_path()
    if not wiki:
        return None

    parts = []
    st = _state(session_id)

    with _lock:
        pending = st["pending"]
        if pending:
            st["pending"] = False
            st["nudges"] += 1

    if pending:
        parts.append(
            "[wiki-hint] Je hebt de voorbije minuten veel opzoekwerk gedaan "
            f"({NUDGE_THRESHOLD}+ research-calls). Evalueer NU of er duurzame "
            "bevindingen zijn (nieuwe feiten, beslissingen, werkende recepten, "
            "bronnen om te bewaren). Zo ja: bewaar ze proactief in de wiki onder "
            f"{wiki} volgens de llm-wiki skill (raw-bron met sha256, "
            "entity/concept-pagina, index.md + log.md bijwerken). Zo nee: ga "
            "verder zonder hierover te melden."
        )

    if is_first_turn:
        parts.append(
            f"[wiki-hint] Er bestaat een llm-wiki (kennisbank) onder {wiki}. "
            "Gebruik die bij opzoekwerk: oriënteer eerst via index.md, SCHEMA.md "
            "en recent log.md voordat je extern onderzoek start of nieuwe "
            "notities maakt. Duurzame bevindingen schrijf je proactief weg naar "
            "de wiki (llm-wiki skill), niet alleen in het antwoord."
        )
    else:
        entries = _index_entries(wiki)
        matches = _best_matches(user_message, entries)
        if matches:
            listing = "\n".join(f"  {m}" for m in matches)
            parts.append(
                f"[wiki-hint] Mogelijk relevante wiki-pagina's onder {wiki} "
                "(lees deze vóór extern opzoekwerk):\n" + listing
            )

    if not parts:
        return None
    return {"context": "\n\n".join(parts)}


def register(ctx):
    ctx.register_hook("pre_llm_call", _pre_llm_call)
    ctx.register_hook("post_tool_call", _post_tool_call)