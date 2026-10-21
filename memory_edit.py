"""Edit a Hermes memory file (MEMORY.md / USER.md) the way Hermes itself does: under its lock.

For the scheduled upkeep run (MAINTENANCE.md), which may edit the memory files of profiles it does
not run in, where the memory tool cannot reach. The plugin's hooks never import this module.

Hermes writes memory under an exclusive flock on ``<file>.lock`` and replaces the file atomically
(tools/memory_tool_store.py). This helper does the same: it takes the lock, re-reads the file,
applies the edits by exact entry text (so entries another session added meanwhile survive), keeps
a backup of the previous version, and writes through a temp file + rename.

    python3 memory_edit.py show <file>                 numbered entries with sizes (JSON)
    python3 memory_edit.py apply <file> <plan.json>    apply a plan; prints the result (JSON)
    python3 memory_edit.py restore <file> <backup>     put a backup back, under the lock (JSON)

A plan: {"remove": ["<exact entry>", ...],
         "replace": [{"old": "<exact entry>", "new": "<text>"}, ...],
         "add": ["<text>", ...],
         "limit": 2200}            (required: refuse a result over this many characters)
Every "old"/"remove" text must match exactly one current entry, otherwise nothing is written.
"""

import contextlib
import datetime
import glob
import json
import os
import sys
import tempfile

ENTRY_DELIMITER = "\n§\n"
KEEP_BACKUPS = 4

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX
    fcntl = None


def parse(raw):
    return [e for e in (x.strip() for x in raw.split(ENTRY_DELIMITER)) if e]


def read_entries(path):
    """Entries as Hermes reads them (utf-8-sig, strict: an undecodable file is an error, not a blank)."""
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8-sig") as f:
        return list(dict.fromkeys(parse(f.read())))


@contextlib.contextmanager
def file_lock(path):
    lock_path = path + ".lock"
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        if fcntl is not None:
            with contextlib.suppress(OSError):
                fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def atomic_write(path, text):
    folder = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix=".mem_", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        with contextlib.suppress(OSError):
            os.chmod(tmp, os.stat(path).st_mode & 0o777 if os.path.exists(path) else 0o600)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _backup(path, today):
    if not os.path.exists(path):
        return None
    target = f"{path}.bak-upkeep-{today}"
    n = 1
    while os.path.exists(target):
        n += 1
        target = f"{path}.bak-upkeep-{today}-{n}"
    mode = os.stat(path).st_mode & 0o777
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode or 0o600)
    with open(path, "rb") as src, os.fdopen(fd, "wb") as dst:
        dst.write(src.read())
    olds = sorted(glob.glob(glob.escape(path) + ".bak-upkeep-*"), key=os.path.getmtime)
    for old in olds[:-KEEP_BACKUPS]:
        with contextlib.suppress(OSError):
            os.unlink(old)
    return target


def _match(entries, text, what):
    text = text.strip()
    hits = [i for i, e in enumerate(entries) if e == text]
    if len(hits) != 1:
        raise ValueError(f"{what}: {len(hits)} entries match exactly (need 1): {text[:80]!r}")
    return hits[0]


def apply(path, plan, today=None):
    today = today or datetime.date.today().isoformat()
    if not isinstance(plan, dict):
        raise ValueError("plan must be an object")
    with file_lock(path):
        entries = read_entries(path)
        before = len(ENTRY_DELIMITER.join(entries))
        new = list(entries)
        for item in plan.get("replace") or []:
            i = _match(new, item["old"], "replace")
            new[i] = str(item["new"]).strip()
        drop = sorted({_match(new, text, "remove") for text in plan.get("remove") or []}, reverse=True)
        for i in drop:
            del new[i]
        for text in plan.get("add") or []:
            text = str(text).strip()
            if text and text not in new:
                new.append(text)
        new = list(dict.fromkeys(e for e in new if e))
        if parse(ENTRY_DELIMITER.join(new)) != new:
            raise ValueError("an entry would not survive a reload (it contains or ends with the delimiter)")
        if entries and not new:
            raise ValueError("refusing to empty the file")
        after = len(ENTRY_DELIMITER.join(new))
        limit = plan.get("limit")
        if not isinstance(limit, int) or limit <= 0:
            raise ValueError("plan needs a positive integer 'limit' (the profile's limit from the audit)")
        if after > limit:
            raise ValueError(f"result is {after} characters, over the limit {limit}; nothing written")
        if new == entries:
            return {"changed": False, "chars_before": before, "chars_after": after, "entries": len(new)}
        backup = _backup(path, today)
        atomic_write(path, ENTRY_DELIMITER.join(new))
    return {"changed": True, "chars_before": before, "chars_after": after, "entries": len(new), "backup": backup}


def restore(path, backup, today=None):
    """Put a backup back, under the lock (an undo). Entries in the current file that the backup lacks
    are returned as ``not_in_backup``: they are either the upkeep run's own new text or memories a
    live session added meanwhile. Re-add the second kind with an ``apply`` plan."""
    with open(backup, encoding="utf-8-sig") as f:
        old = list(dict.fromkeys(parse(f.read())))
    with file_lock(path):
        current = read_entries(path)
        saved = _backup(path, today or datetime.date.today().isoformat())
        atomic_write(path, ENTRY_DELIMITER.join(old))
    return {"restored": backup, "backup_of_current": saved,
            "chars_before": len(ENTRY_DELIMITER.join(current)), "chars_after": len(ENTRY_DELIMITER.join(old)),
            "not_in_backup": [e for e in current if e not in old]}


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) == 2 and args[0] == "show":
        entries = read_entries(args[1])
        print(json.dumps({"file": args[1], "chars": len(ENTRY_DELIMITER.join(entries)),
                          "entries": [{"n": i + 1, "chars": len(e), "text": e} for i, e in enumerate(entries)]},
                         ensure_ascii=False, indent=1))
        return 0
    if len(args) == 3 and args[0] == "apply":
        try:
            with open(args[2], encoding="utf-8") as f:
                plan = json.load(f)
            print(json.dumps(apply(args[1], plan), ensure_ascii=False))
            return 0
        except (ValueError, KeyError, OSError, UnicodeDecodeError) as exc:
            print(json.dumps({"changed": False, "error": str(exc)}, ensure_ascii=False))
            return 1
    if len(args) == 3 and args[0] == "restore":
        try:
            print(json.dumps(restore(args[1], args[2]), ensure_ascii=False))
            return 0
        except (ValueError, OSError, UnicodeDecodeError) as exc:
            print(json.dumps({"restored": False, "error": str(exc)}, ensure_ascii=False))
            return 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
