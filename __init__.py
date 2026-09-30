"""wiki-hint - Hermes plugin.

Two hooks:
- pre_llm_call: injects a short wiki reminder plus the most relevant wiki
  pages (keyword overlap with the user message), plus a one-shot "save this"
  nudge after heavy research.
- post_tool_call: counts research tool calls per session and arms the nudge.

The plugin NEVER writes to the wiki itself; it only nudges the model toward
the llm-wiki skill workflow (orient first, raw source + sha256, update
index.md and log.md).

Matching v2: tokens are split on hyphens (wiki slugs like
"pool-build-quote" count as the separate words pool/build/quote), compared
prefix-tolerantly so inflected forms (pump/pumps) match, and loose words
also match inside compounds (pump in "sandfilterpump"). A single
overlapping term suffices when it is rare across the index. Multimodal
user messages (list-of-parts) are flattened to text instead of ignored.
"""

import os
import re
import threading

WIKI_FALLBACK = os.path.expanduser("~/wiki")
RESEARCH_TOOLS = {"web_search", "web_extract", "browser_exec", "session_search"}
NUDGE_THRESHOLD = 5          # research calls before the save nudge fires
MAX_NUDGES_PER_SESSION = 3
MAX_INJECT_MATCHES = 3
MAX_LINE_CHARS = 220         # truncation per injected index line
MIN_WORD_LEN = 3             # shorter (sub)words are noise
MIN_OVERLAP = 2              # content terms that must overlap
FUZZY_PREFIX = 5             # shared-prefix length for a fuzzy match
MIN_SUBSTR_LEN = 6           # min length for substring matching (pump in sandfilterpump)
RARE_DF_MAX = 3              # word in <= this many index pages counts as rare

STOPWORDS = {
    # dutch
    "de", "het", "een", "van", "en", "dat", "die", "met", "voor", "aan", "over",
    "naar", "op", "in", "uit", "bij", "als", "is", "ben", "zijn", "was", "were",
    "heb", "hebt", "hebben", "had", "would", "zou", "kun", "kan", "kunnen",
    "wordt", "worden", "werd", "maar", "ook", "nog", "al", "net", "wel", "niet",
    "wat", "hoe", "waar", "wanneer", "waarom", "wie", "mijn", "jouw", "zijn",
    "ons", "jullie", "daar", "hier", "daarna", "toen", "even", "gewoon", "zelf",
    "meest", "veel", "meer", "goed", "nieuw", "nieuwe", "blijf", "graag",
    "welke", "welk", "alle", "alles", "geen", "doen", "doe", "done",
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
_session_state = {}  # session_id -> {"count": int, "nudges": int, "pending": bool}


def _wiki_path():
    p = os.environ.get("WIKI_PATH", "").strip()
    if p and os.path.isdir(p):
        return p
    if os.path.isdir(WIKI_FALLBACK):
        return WIKI_FALLBACK
    return None


def _flatten_message(message):
    """pre_llm_call may receive a multimodal list (text/image parts); extract
    the text parts so turns with photos still match."""
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
    """Drop short words and stopwords; hyphenated slugs are split into their
    parts AND kept whole, so "fan-curves" matches "fan-curves", "fan" and
    "curves" alike."""
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
    """Equal, shared prefix >= FUZZY_PREFIX, the shorter is a prefix of the
    longer (pump/pumps), or the shorter is contained in the longer (pump in
    sandfilterpump). Substring containment requires both sides to be at
    least MIN_SUBSTR_LEN chars."""
    if w == v:
        return True
    if len(w) < MIN_WORD_LEN or len(v) < MIN_WORD_LEN:
        return False
    if w in v or v in w:
        return len(w) >= MIN_SUBSTR_LEN and len(v) >= MIN_SUBSTR_LEN
    p = os.path.commonprefix([w, v])
    if p and (p == w or p == v):
        return True
    m = min(len(w), len(v))
    return len(p) >= FUZZY_PREFIX and len(p) >= m - 2


def _overlap(a, b):
    """Number of terms from a with at least one (fuzzy) match in b."""
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
            short = line.strip()[:MAX_LINE_CHARS]
            entries.append((title, _tokenize(slug), _tokenize(line), short))
        _index_cache["path"] = idx
        _index_cache["mtime"] = mtime
        _index_cache["entries"] = entries
        return entries


def _best_matches(user_message, entries):
    """Top index lines: overlap >= MIN_OVERLAP, or overlap == 1 with a rare
    word (document frequency <= RARE_DF_MAX across the index). Deterministic
    order via a sorted match-key tiebreaker."""
    q = _tokenize(user_message or "")
    if not q:
        return []
    hit_lists = []  # (matched query words, line)
    for title, slug_tokens, tokens, line in entries:
        matched = tuple(w for w in q if any(_fuzzy_eq(w, v) for v in tokens))
        if matched:
            hit_lists.append((matched, slug_tokens, line))
    if not hit_lists:
        return []
    df = {}
    for matched, _, _ in hit_lists:
        for w in matched:
            df[w] = df.get(w, 0) + 1
    scored = []
    for matched, slug_tokens, line in hit_lists:
        single = len(matched) < MIN_OVERLAP
        if single:
            w = matched[0]
            if df[w] > RARE_DF_MAX or not any(
                _fuzzy_eq(w, v) for v in slug_tokens
            ):
                continue
        scored.append((len(matched), " ".join(sorted(matched)), line))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [line for _, _, line in scored[:MAX_INJECT_MATCHES]]


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

    # Page suggestions on every turn (including the first) with a relevant question
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
