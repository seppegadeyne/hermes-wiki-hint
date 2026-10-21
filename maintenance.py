"""Read-only health audit of an llm-wiki vault and of Hermes' always-loaded memory files.

Nothing in this module writes. It produces a report (text or JSON) that a person, or a scheduled
agent run following MAINTENANCE.md, uses to do the repairs (with a backup first).

Wiki checks: index lines that point to missing pages, content pages missing from the index,
index lines over the length limit, broken wikilinks (code spans ignored), required frontmatter
fields, tags outside the SCHEMA.md taxonomy, pages over the line limit, log.md rotation, raw/
sha256 drift and orphan pages.

Memory checks, per Hermes home (the active one plus its profiles): MEMORY.md and USER.md size
against the limits in that home's config.yaml (counted the way Hermes counts them), long entries,
near-duplicate entries, and the size of SOUL.md.

Usage: python3 maintenance.py [--wiki PATH] [--json] [--home PATH ...]
Configuration (env, same as the hooks): WIKI_PATH, plus optional WIKI_HINT_INDEX_LINE_MAX (220),
WIKI_HINT_PAGE_MAX_LINES (200), WIKI_HINT_LOG_MAX_ENTRIES (500), WIKI_HINT_LOG_ROTATION
("entries" or "monthly"), WIKI_HINT_ARCHIVE_GLOB (comma-separated globs of page paths that are
archives by design, e.g. "*-statuslog-*": exempt from the index and page-size checks),
WIKI_HINT_MAINT_HOMES (comma-separated Hermes homes; default: the active home and its profiles).
"""

import argparse
import datetime
import fnmatch
import hashlib
import json
import os
import re
import sys

CONTENT_DIRS = ("entities", "concepts", "comparisons", "queries")
REQUIRED_FRONTMATTER = ("title", "created", "updated", "type", "tags")
SKIP_DIRS = {"_archive", "raw", "node_modules"}  # raw/ is checked separately (hashes only)
DEFAULTS = {"WIKI_HINT_INDEX_LINE_MAX": 220, "WIKI_HINT_PAGE_MAX_LINES": 200, "WIKI_HINT_LOG_MAX_ENTRIES": 500}
LIST_LIMIT = 25                # items listed per check; the count is always complete
LONG_ENTRY_CHARS = 400         # a memory entry this long is a candidate to move into the wiki
MEMORY_FULL_PCT = 90           # at or above this share of the limit a memory file needs attention
DUPLICATE_JACCARD = 0.5        # word overlap at which two memory entries count as near-duplicates
ENTRY_DELIMITER = "\n§\n"      # Hermes' memory entry separator (tools/memory_tool_store.py)
MEMORY_DEFAULT_LIMITS = {"MEMORY.md": ("memory_char_limit", 2200), "USER.md": ("user_char_limit", 1375)}

WIKILINK_RE = re.compile(r"\[\[([^\]|#\n]+)")
FENCE_RE = re.compile(r"```.*?```", re.S)
INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
LOG_HEAD_RE = re.compile(r"^## \[(\d{4}-\d{2}-\d{2})\]", re.M)
INDEX_LINE_RE = re.compile(r"^\s*-\s*\[\[([^\]|#]+)")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
WORD_RE = re.compile(r"[a-z0-9][a-z0-9._/-]{2,}")
# Paths under the user's home quoted in a memory entry (~/... or $HOME/...). Absolute paths are not
# checked: /etc, /srv or /root in a memory entry usually live on a remote server. Placeholders
# (<p>, *, {x}) and URLs are skipped, and a path ends at whitespace.
PATH_RE = re.compile(r"(?<![\w/:.@])((?:~|\$HOME)/[^\s,;:)'\"`<>*{}\[\]$]+)")


# ------------------------------------------------------------------ helpers


def _int_env(name):
    raw = os.environ.get(name, "").strip().strip("\"'")
    try:
        value = int(raw)
    except ValueError:
        return DEFAULTS[name]
    return value if value > 0 else DEFAULTS[name]


def _read(path, encoding="utf-8"):
    try:
        with open(path, encoding=encoding, errors="replace") as f:
            return f.read()
    except OSError:
        return None


def _check(items):
    """One check result: the full count and the first LIST_LIMIT items."""
    return {"count": len(items), "items": items[:LIST_LIMIT]}


def frontmatter(text):
    """(fields, ok): simple ``key: value`` fields of a leading ``---`` block; ok is False without one."""
    if not text.startswith("---\n"):
        return {}, False
    m = re.search(r"\n---[ \t]*(?:\n|$)", text[3:])
    if not m:
        return {}, False
    end = m.start() + 3
    fields = {}
    for line in text[4:end].splitlines():
        m = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if m:
            fields[m.group(1)] = m.group(2).strip()
    return fields, True


def _tags(value):
    value = value.strip().strip("[]")
    return [t.strip().strip("\"'") for t in value.split(",") if t.strip().strip("\"'")]


def _archive_globs():
    raw = os.environ.get("WIKI_HINT_ARCHIVE_GLOB", "").strip().strip("\"'")
    return [g.strip() for g in raw.split(",") if g.strip()]


def _is_archive(page, globs):
    return any(fnmatch.fnmatch(page, g) for g in globs)


def _strip_code(text):
    return INLINE_CODE_RE.sub(" ", FENCE_RE.sub(" ", text))


def _pages(wiki):
    """Relative paths of the vault's markdown pages (hidden folders, _archive and raw/ left out)."""
    out = []
    for root, dirs, files in os.walk(wiki):
        rel_root = os.path.relpath(root, wiki)
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and not (rel_root == "." and d in SKIP_DIRS))
        for name in sorted(files):
            if name.endswith(".md"):
                out.append(os.path.normpath(os.path.join(rel_root, name)))
    return out


def _taxonomy(schema_text):
    """Tags named (in backticks) under SCHEMA.md's '## Tag Taxonomy' heading; None when there is none."""
    if not schema_text:
        return None
    m = re.search(r"^## Tag Taxonomy\s*$(.*?)(?=^## |\Z)", schema_text, re.M | re.S)
    if not m:
        return None
    tags = set(re.findall(r"`([a-z0-9][a-z0-9-]*)`", m.group(1)))
    return tags or None


# ------------------------------------------------------------------ wiki


def audit_wiki(wiki, today=None):
    today = today or datetime.date.today()
    line_max = _int_env("WIKI_HINT_INDEX_LINE_MAX")
    page_max = _int_env("WIKI_HINT_PAGE_MAX_LINES")
    pages = _pages(wiki)
    texts = {p: _read(os.path.join(wiki, p)) or "" for p in pages}
    slugs = {p[:-3] for p in pages}
    basenames = {}
    for p in pages:
        basenames.setdefault(os.path.basename(p)[:-3], p)

    def resolves(link):
        link = link.strip()
        if not link:
            return True
        if link in slugs or link[:-3] in slugs or link in basenames:
            return True
        return os.path.exists(os.path.join(wiki, link)) or os.path.exists(os.path.join(wiki, link + ".md"))

    content = [p for p in pages if p.split(os.sep)[0] in CONTENT_DIRS]
    globs = _archive_globs()
    archives = {p for p in content if _is_archive(p[:-3], globs)}
    index_text = texts.get("index.md", "")
    index_links, long_lines, seen, duplicates = [], [], set(), []
    for line in index_text.splitlines():
        m = INDEX_LINE_RE.match(line)
        if not m:
            continue
        target = m.group(1).strip()
        index_links.append(target)
        if len(line.rstrip()) > line_max:
            long_lines.append({"page": target, "chars": len(line.rstrip())})
        if target in seen:
            duplicates.append(target)
        seen.add(target)
    indexed = set(index_links)

    inbound = {p[:-3]: set() for p in content}
    broken = []
    for p in pages:
        for link in WIKILINK_RE.findall(_strip_code(texts[p])):
            link = link.strip()
            if not resolves(link):
                broken.append({"page": p, "target": link})
                continue
            target = link[:-3] if link.endswith(".md") else link
            target = target if target in inbound else (basenames.get(target, "")[:-3] or target)
            if target in inbound and p != "index.md" and p[:-3] != target:
                inbound[target].add(p)

    not_indexed = [{"page": p[:-3], "linked_from": len(inbound.get(p[:-3], ()))}
                   for p in content if p[:-3] not in indexed and not (p in archives and inbound.get(p[:-3]))]
    missing = [t for t in dict.fromkeys(index_links) if not resolves(t)]
    orphans = [p[:-3] for p in content if not inbound.get(p[:-3]) and p[:-3] not in indexed]

    taxonomy = _taxonomy(texts.get("SCHEMA.md"))
    missing_fields, unknown_tags = [], {}
    over_limit = []
    for p in content:
        fields, ok = frontmatter(texts[p])
        absent = list(REQUIRED_FRONTMATTER) if not ok else [k for k in REQUIRED_FRONTMATTER if not fields.get(k)]
        if absent:
            missing_fields.append({"page": p[:-3], "fields": absent})
        if taxonomy is not None and fields.get("tags"):
            for tag in _tags(fields["tags"]):
                if tag not in taxonomy:
                    unknown_tags.setdefault(tag, []).append(p[:-3])
        lines = texts[p].count("\n") + 1
        if lines > page_max and p not in archives:
            over_limit.append({"page": p[:-3], "lines": lines})
    over_limit.sort(key=lambda x: -x["lines"])
    tags = [{"tag": t, "pages": len(ps), "example": ps[0]} for t, ps in sorted(unknown_tags.items())]

    return {
        "path": wiki,
        "pages": len(pages),
        "content_pages": len(content),
        "index_missing_pages": _check(missing),
        "index_not_indexed": _check(not_indexed),
        "index_long_lines": _check(long_lines),
        "index_duplicates": _check(duplicates),
        "broken_links": _check(broken),
        "frontmatter_missing": _check(missing_fields),
        "unknown_tags": _check(tags),
        "pages_over_limit": _check(over_limit),
        "orphans": _check(orphans),
        "log": _audit_log(texts.get("log.md", ""), today),
        "raw": _audit_raw(wiki),
        "archives": len(archives),
        "limits": {"index_line_max": line_max, "page_max_lines": page_max},
    }


def _audit_log(text, today):
    dates = LOG_HEAD_RE.findall(text)
    max_entries = _int_env("WIKI_HINT_LOG_MAX_ENTRIES")
    monthly = os.environ.get("WIKI_HINT_LOG_ROTATION", "").strip().strip("\"'").lower() == "monthly"
    oldest = min(dates) if dates else None
    reason = None
    if len(dates) > max_entries:
        reason = f"{len(dates)} entries > {max_entries}"
    elif monthly and oldest and oldest[:7] < today.strftime("%Y-%m"):
        reason = f"entries from {oldest[:7]} (monthly rotation)"
    return {"entries": len(dates), "oldest": oldest, "newest": max(dates) if dates else None,
            "rotation_due": reason is not None, "reason": reason}


def _audit_raw(wiki):
    """Recompute the sha256 of every raw/ file that records one (body = everything after the closing
    '---' line). Reads bytes, so the hash matches what is on disk."""
    drift, unparsable, checked = [], [], 0
    raw_root = os.path.join(wiki, "raw")
    for root, dirs, files in os.walk(raw_root):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for name in sorted(files):
            if not name.endswith(".md"):
                continue
            path = os.path.join(root, name)
            rel = os.path.relpath(path, wiki)
            try:
                with open(path, "rb") as f:
                    data = f.read()
            except OSError:
                continue
            if not data.startswith(b"---\n"):
                continue
            end = data.find(b"\n---\n", 3)
            if end < 0:
                continue
            head = data[4:end].decode("utf-8", "replace")
            m = re.search(r"^sha256:\s*(\S+)", head, re.M)
            if not m:
                continue
            recorded = m.group(1).strip("\"'").lower()
            if not SHA_RE.match(recorded):
                unparsable.append({"file": rel, "value": m.group(1)[:40]})
                continue
            checked += 1
            body = data[end + 5:]
            # Two body forms are in use: exactly as stored, and without the blank line(s) that usually
            # follow the frontmatter. Either one matching means the file is unchanged.
            actual = hashlib.sha256(body).hexdigest()
            if recorded not in (actual, hashlib.sha256(body.lstrip(b"\r\n")).hexdigest()):
                drift.append({"file": rel, "recorded": recorded[:12], "actual": actual[:12]})
    return {"checked": checked, "drift": _check(drift), "unparsable": _check(unparsable)}


# ------------------------------------------------------------------ memory files


def active_home():
    return os.path.expanduser(os.environ.get("HERMES_HOME", "").strip() or "~/.hermes")


def hermes_homes():
    """The active Hermes home and its profiles (a profile home resolves to its parent's set)."""
    raw = os.environ.get("WIKI_HINT_MAINT_HOMES", "").strip().strip("\"'")
    if raw:
        return [os.path.expanduser(p.strip()) for p in raw.split(",") if p.strip()]
    home = active_home()
    parent = os.path.dirname(os.path.normpath(home))
    if os.path.basename(parent) == "profiles":
        home = os.path.dirname(parent)
    homes = [home]
    profiles = os.path.join(home, "profiles")
    if os.path.isdir(profiles):
        homes += [os.path.join(profiles, d) for d in sorted(os.listdir(profiles))
                  if os.path.isdir(os.path.join(profiles, d, "memories"))]
    return homes


def _memory_limits(home):
    limits = {name: default for name, (_, default) in MEMORY_DEFAULT_LIMITS.items()}
    text = _read(os.path.join(home, "config.yaml")) or ""
    block = re.search(r"^memory:[ \t]*\n((?:[ \t]+.*\n?)*)", text, re.M)
    if block:
        for name, (key, _) in MEMORY_DEFAULT_LIMITS.items():
            m = re.search(rf"^[ \t]+{key}:\s*(\d+)", block.group(1), re.M)
            if m:
                limits[name] = int(m.group(1))
    return limits


def _entries(text):
    """Entries the way Hermes loads them: split on the delimiter, stripped, exact duplicates dropped."""
    return list(dict.fromkeys(e for e in (x.strip() for x in (text or "").split(ENTRY_DELIMITER)) if e))


def _words(text):
    return set(WORD_RE.findall(text.lower()))


def _near_duplicates(entries):
    pairs = []
    words = [_words(e) for e in entries]
    for i in range(len(entries)):
        for j in range(i + 1, len(entries)):
            a, b = words[i], words[j]
            if not a or not b:
                continue
            if entries[i] in entries[j] or entries[j] in entries[i] \
                    or len(a & b) / len(a | b) >= DUPLICATE_JACCARD:
                pairs.append([i + 1, j + 1])
    return pairs


def _missing_paths(entry):
    """Local paths an entry names that do not exist (a renamed or deleted folder makes the entry stale)."""
    out = []
    for raw in PATH_RE.findall(entry):
        path = raw.rstrip(".")
        local = os.path.expanduser(path.replace("$HOME", "~", 1))
        if os.path.exists(local):
            continue
        # "dir/file1, file2" or "repo/{a,b}" shorthands: a missing leaf under an existing folder is ambiguous
        if not os.path.isdir(os.path.dirname(local)):
            out.append(path)
        elif "." not in os.path.basename(path):
            out.append(path)
    return list(dict.fromkeys(out))


def audit_memory(homes=None):
    out = []
    for home in homes or hermes_homes():
        parent = os.path.basename(os.path.dirname(os.path.normpath(home)))
        label = os.path.basename(os.path.normpath(home)) if parent == "profiles" else "default"
        limits = _memory_limits(home)
        files = []
        for name, limit in limits.items():
            text = _read(os.path.join(home, "memories", name), encoding="utf-8-sig")
            if text is None:
                continue
            entries = _entries(text)
            chars = len(ENTRY_DELIMITER.join(entries))
            pct = int(chars * 100 / limit) if limit else 0
            status = "over" if chars > limit else ("full" if pct >= MEMORY_FULL_PCT else "ok")
            files.append({
                "file": name, "chars": chars, "limit": limit, "pct": pct, "entries": len(entries),
                "status": status,
                "long_entries": [{"entry": i + 1, "chars": len(e), "start": " ".join(e.split())[:80]}
                                 for i, e in enumerate(entries) if len(e) >= LONG_ENTRY_CHARS],
                "near_duplicates": _near_duplicates(entries),
                "missing_paths": [{"entry": i + 1, "paths": m} for i, e in enumerate(entries)
                                  if (m := _missing_paths(e))],
            })
        soul = _read(os.path.join(home, "SOUL.md"))
        out.append({"home": label, "files": files, "soul_chars": len(soul) if soul is not None else None})
    return out


# ------------------------------------------------------------------ report


def audit(wiki=None, homes=None, today=None):
    """The full report. ``attention`` lists the checks that need work, so a caller can stop early."""
    report: dict = {"generated": (today or datetime.date.today()).isoformat()}
    if wiki and os.path.isdir(wiki):
        report["wiki"] = audit_wiki(wiki, today)
    report["memory"] = audit_memory(homes)
    attention = []
    w = report.get("wiki")
    if w:
        for key in ("index_missing_pages", "index_not_indexed", "index_long_lines", "index_duplicates",
                    "broken_links", "frontmatter_missing", "unknown_tags", "pages_over_limit", "orphans"):
            if w[key]["count"]:
                attention.append(f"wiki.{key}: {w[key]['count']}")
        if w["log"]["rotation_due"]:
            attention.append(f"wiki.log: rotation due ({w['log']['reason']})")
        if w["raw"]["drift"]["count"] or w["raw"]["unparsable"]["count"]:
            attention.append(f"wiki.raw: {w['raw']['drift']['count']} drift, "
                             f"{w['raw']['unparsable']['count']} unparsable")
    for home in report["memory"]:
        for f in home["files"]:
            if f["status"] != "ok" or f["near_duplicates"] or f["missing_paths"]:
                # No live character count here: these lines feed change detection, and any memory
                # write would change a count. The status (ok/full/over) only moves at a threshold.
                attention.append(f"memory.{home['home']}.{f['file']}: {f['status']}"
                                 + (f", {len(f['near_duplicates'])} near-duplicate pair(s)"
                                    if f["near_duplicates"] else "")
                                 + (f", missing home paths in {len(f['missing_paths'])} entries"
                                    if f["missing_paths"] else ""))
    report["attention"] = attention
    return report


def render_text(report, entry_text=True):
    lines = [f"wiki-hint audit {report['generated']}"]
    w = report.get("wiki")
    if w:
        lines.append(f"Wiki {w['path']}: {w['pages']} pages ({w['content_pages']} content, "
                     f"{w['archives']} archive pages)")
        for key, label in (("index_missing_pages", "index -> missing page"),
                           ("index_not_indexed", "not in index"),
                           ("index_long_lines", f"index lines > {w['limits']['index_line_max']}"),
                           ("index_duplicates", "duplicate index lines"),
                           ("broken_links", "broken wikilinks"),
                           ("frontmatter_missing", "frontmatter fields missing"),
                           ("unknown_tags", "tags outside the taxonomy"),
                           ("pages_over_limit", f"pages > {w['limits']['page_max_lines']} lines"),
                           ("orphans", "orphans (no inbound link, not indexed)")):
            check = w[key]
            if not check["count"]:
                continue
            lines.append(f"  {label}: {check['count']}")
            for item in check["items"][:5]:
                lines.append("    - " + (json.dumps(item, ensure_ascii=False) if isinstance(item, dict) else item))
        log = w["log"]
        lines.append(f"  log.md: {log['entries']} entries ({log['oldest']} .. {log['newest']})"
                     + (f", rotation due: {log['reason']}" if log["rotation_due"] else ""))
        raw = w["raw"]
        lines.append(f"  raw/: {raw['checked']} hashes checked, {raw['drift']['count']} drift, "
                     f"{raw['unparsable']['count']} unparsable")
    else:
        lines.append("Wiki: not found (set WIKI_PATH)")
    for home in report["memory"]:
        parts = [f"{f['file']} {f['chars']}/{f['limit']} {f['pct']}% {f['status']}" for f in home["files"]]
        soul = f", SOUL.md {home['soul_chars']} chars" if home["soul_chars"] is not None else ""
        lines.append(f"Memory [{home['home']}]: " + ("; ".join(parts) or "no memory files") + soul)
        for f in home["files"]:
            for item in f["missing_paths"]:
                lines.append(f"    {f['file']} entry {item['entry']}: missing "
                             + (", ".join(item["paths"]) if entry_text else f"{len(item['paths'])} path(s)"))
    lines.append("Needs attention: " + ("; ".join(report["attention"]) if report["attention"] else "nothing"))
    return "\n".join(lines)


def wiki_path():
    p = os.environ.get("WIKI_PATH", "").strip().strip("\"'").strip()
    p = os.path.expanduser(p) if p else os.path.expanduser("~/wiki")
    return p if os.path.isdir(p) else None


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only audit of an llm-wiki vault and Hermes memory files.")
    parser.add_argument("--wiki", help="wiki root (default: WIKI_PATH, then ~/wiki)")
    parser.add_argument("--home", action="append", help="Hermes home to check (repeatable; default: active + profiles)")
    parser.add_argument("--json", action="store_true", help="print JSON instead of text")
    parser.add_argument("--attention", action="store_true",
                        help="print only the checks that need work, one per line (stable output for change detection)")
    args = parser.parse_args(argv)
    report = audit(os.path.expanduser(args.wiki) if args.wiki else wiki_path(), args.home)
    if args.attention:
        print("\n".join(report["attention"]))
    else:
        print(json.dumps(report, ensure_ascii=False, indent=1) if args.json else render_text(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
