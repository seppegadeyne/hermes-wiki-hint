"""``hermes wiki-hint upkeep install|status|remove``: set up the weekly upkeep cron job in one command.

Hermes plugins cannot schedule anything themselves, so the upkeep (MAINTENANCE.md) runs as an ordinary
cron job of the profile the command runs in. ``install`` writes two small wrapper scripts into that
profile's ``scripts/`` folder (the audit as JSON for the agent, and the stable ``--attention`` list as the
change monitor) and creates the job, or updates it when it already exists (found by its report script),
so running it again is safe. The wrappers call ``maintenance.py`` of the installed plugin, so plugin
updates need no reinstall.
"""

import os
import re
import shlex
import sys

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
REPORT_SCRIPT = "wiki_upkeep_report.sh"
MONITOR_SCRIPT = "wiki_upkeep_monitor.sh"
JOB_NAME = "wiki-hint upkeep (weekly)"
DEFAULT_SCHEDULE = "30 5 * * 0"   # Sunday 05:30 in the profile's timezone
SKILLS = ["llm-wiki"]

PROMPTS = {
    "en": """Weekly upkeep of the llm-wiki at {wiki} and of the Hermes memory files listed in the audit report.

Follow {plugin}/MAINTENANCE.md step by step (read it fully first). The audit report (JSON) is above as script output; if its "attention" list is empty, answer only "Nothing to do." and stop.

Rules:
- Headless: ask no questions. Decide within the limits of MAINTENANCE.md; report doubtful cases instead of changing them.
- The operator authorizes this run to edit memories/MEMORY.md and memories/USER.md of every Hermes home in the report, only through python3 {plugin}/memory_edit.py (show/apply/restore). Touch nothing else in other profiles.
- Back up the vault to {backups} before the first change.
- Follow the llm-wiki skill and the vault's SCHEMA.md for index, log and frontmatter conventions.{archive}
- Final report, short: what changed (before/after characters per memory file), what was left and why.
""",
    "nl": """Wekelijks onderhoud van de llm-wiki op {wiki} en van de Hermes-memorybestanden uit het auditrapport.

Volg {plugin}/MAINTENANCE.md stap voor stap (lees het eerst volledig). Het auditrapport (JSON) staat hierboven als scriptoutput; is "attention" leeg, antwoord dan alleen "Niets te doen." en stop.

Afspraken:
- Headless: stel geen vragen. Beslis binnen de grenzen van MAINTENANCE.md; meld twijfelgevallen in het verslag in plaats van ze te wijzigen.
- De operator geeft deze run toestemming om memories/MEMORY.md en memories/USER.md van elke Hermes-home in het rapport aan te passen, uitsluitend via python3 {plugin}/memory_edit.py (show/apply/restore). Niets anders in andere profielen aanraken.
- Vault-backup naar {backups} vóór de eerste wijziging.
- Volg de llm-wiki skill en SCHEMA.md van de vault voor index-, log- en frontmatterconventies.{archive}
- Verslag in het Nederlands, kort: wat veranderd is (voor/na-tekens per memorybestand), wat bleef staan en waarom.
""",
}
ARCHIVE_NOTE = {"en": "\n- Pages matching {glob} are archives by design.",
                "nl": "\n- Pagina's die op {glob} passen zijn archief by design."}


def _env(name):
    return os.environ.get(name, "").strip().strip("\"'").strip()


def _home():
    return os.path.abspath(os.path.expanduser(_env("HERMES_HOME") or "~/.hermes"))


def _is_profile(home):
    return os.path.basename(os.path.dirname(os.path.normpath(home))) == "profiles"


def _lang():
    return "nl" if _env("WIKI_HINT_LANG").lower() == "nl" else "en"


def _cron_mode(home):
    try:
        with open(os.path.join(home, "config.yaml"), encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return None
    m = re.search(r"^approvals:[ \t]*\n((?:[ \t]+.*\n?)*)", text, re.M)
    hit = m and re.search(r"^[ \t]+cron_mode:\s*(\S+)", m.group(1), re.M)
    return hit.group(1).strip("\"'") if hit else None


def wrapper(flag, wiki, homes, archive_glob):
    lines = ["#!/usr/bin/env bash",
             f"# wiki-hint upkeep: read-only audit ({flag}). Written by `hermes wiki-hint upkeep install`;",
             "# rerun that command instead of editing this file.",
             f"export WIKI_PATH={shlex.quote(wiki)}",
             f"export WIKI_HINT_MAINT_HOMES={shlex.quote(','.join(homes))}"]
    if archive_glob:
        lines.append(f"export WIKI_HINT_ARCHIVE_GLOB={shlex.quote(archive_glob)}")
    for name in ("WIKI_HINT_INDEX_LINE_MAX", "WIKI_HINT_PAGE_MAX_LINES", "WIKI_HINT_LOG_MAX_ENTRIES",
                 "WIKI_HINT_LOG_ROTATION"):
        if _env(name):
            lines.append(f"export {name}={shlex.quote(_env(name))}")
    # maintenance.py is stdlib-only: the system python3 does, and survives Hermes updates (Hermes' own
    # interpreter lives under a versioned path). Fall back to the interpreter install ran under.
    lines.append(f"PY=$(command -v python3 || echo {shlex.quote(sys.executable or 'python3')})")
    lines.append(f"exec \"$PY\" {shlex.quote(os.path.join(PLUGIN_DIR, 'maintenance.py'))} {flag}")
    return "\n".join(lines) + "\n"


def plan(args):
    """Everything install would write, without writing it (also used by --dry-run and the tests)."""
    home = _home()
    wiki = os.path.abspath(os.path.expanduser(args.wiki or _env("WIKI_PATH") or "~/wiki"))
    if not os.path.isdir(wiki):
        raise SystemExit(f"wiki not found: {wiki} (set WIKI_PATH in this profile's .env or pass --wiki)")
    scope = args.homes or ("self" if _is_profile(home) else "all")
    if scope == "self":
        homes = [home]
    else:
        import importlib.util
        spec = importlib.util.spec_from_file_location("wiki_hint_maint_install",
                                                      os.path.join(PLUGIN_DIR, "maintenance.py"))
        assert spec is not None and spec.loader is not None
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        saved = os.environ.pop("WIKI_HINT_MAINT_HOMES", None)
        try:
            homes = m.hermes_homes()
        finally:
            if saved is not None:
                os.environ["WIKI_HINT_MAINT_HOMES"] = saved
    archive_glob = args.archive_glob if args.archive_glob is not None else _env("WIKI_HINT_ARCHIVE_GLOB")
    lang = _lang()
    backups = os.path.join(home, "backups", "wiki-upkeep")
    prompt = PROMPTS[lang].format(wiki=wiki, plugin=PLUGIN_DIR, backups=backups,
                                  archive=ARCHIVE_NOTE[lang].format(glob=archive_glob) if archive_glob else "")
    return {
        "home": home, "wiki": wiki, "homes": homes, "schedule": args.schedule or DEFAULT_SCHEDULE,
        "scripts": {REPORT_SCRIPT: wrapper("--json", wiki, homes, archive_glob),
                    MONITOR_SCRIPT: wrapper("--attention", wiki, homes, archive_glob)},
        "job": {"prompt": prompt, "name": JOB_NAME, "script": REPORT_SCRIPT, "monitor_script": MONITOR_SCRIPT,
                "skills": list(SKILLS), "deliver": args.deliver or "local", "workdir": wiki},
    }


# ------------------------------------------------------------------ cron store access


def _cron():
    from cron import jobs  # Hermes' own job store; per profile (bound to HERMES_HOME)
    return jobs


def find_job(jobs_mod):
    return [j for j in jobs_mod.list_jobs(include_disabled=True) if j.get("script") == REPORT_SCRIPT]


def install(args, out=print):
    p = plan(args)
    if args.dry_run:
        out(f"Would write to {os.path.join(p['home'], 'scripts')}: {', '.join(p['scripts'])}")
        out(f"Would schedule '{p['schedule']}' in {p['home']} for wiki {p['wiki']}; homes: {', '.join(p['homes'])}")
        out("Prompt:\n" + p["job"]["prompt"])
        return 0
    scripts_dir = os.path.join(p["home"], "scripts")
    os.makedirs(scripts_dir, exist_ok=True)
    for name, text in p["scripts"].items():
        path = os.path.join(scripts_dir, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(path, 0o755)
    jobs_mod = _cron()
    existing = find_job(jobs_mod)
    fields = dict(p["job"], schedule=p["schedule"])
    if existing:
        job = existing[0]
        jobs_mod.update_job(job["id"], fields)
        if args.paused and job.get("enabled", True):
            jobs_mod.pause_job(job["id"], reason="paused by wiki-hint upkeep install --paused")
        action = "updated"
        job_id = job["id"]
    else:
        job = jobs_mod.create_job(paused=bool(args.paused),
                                  paused_reason="created with --paused" if args.paused else None, **fields)
        action = "created"
        job_id = job["id"]
    job = jobs_mod.get_job(job_id) or {}
    state = "paused" if not job.get("enabled", True) else "active"
    out(f"Upkeep job {job_id} {action} ({state}), schedule '{p['schedule']}', wiki {p['wiki']}")
    out(f"Memory files of: {', '.join(p['homes'])}")
    if state == "paused":
        out(f"Start it with: hermes cron resume {job_id}   (try it first with: hermes cron run {job_id})")
    mode = _cron_mode(p["home"])
    if mode != "approve":
        out(f"Note: approvals.cron_mode is {mode or 'unset (deny)'}; the run needs the terminal "
            "(memory_edit.py, tar) without a person to approve. Check that it is allowed.")
    for extra in existing[1:]:
        out(f"Warning: a second upkeep job {extra['id']} exists; remove it with `hermes wiki-hint upkeep remove`.")
    return 0


def status(args, out=print):
    jobs_mod = _cron()
    found = find_job(jobs_mod)
    if not found:
        out("No upkeep job in this profile. Create one with: hermes wiki-hint upkeep install")
        return 1
    for job in found:
        state = "paused" if not job.get("enabled", True) else "active"
        last = job.get("latest_execution") or {}
        out(f"{job['id']} {state} schedule='{(job.get('schedule') or {}).get('expr', job.get('schedule'))}' "
            f"next={job.get('next_run_at')} last={job.get('last_run_at')} status={job.get('last_status')} "
            f"execution={last.get('status') if isinstance(last, dict) else last}")
    return 0


def remove(args, out=print):
    jobs_mod = _cron()
    found = find_job(jobs_mod)
    for job in found:
        jobs_mod.remove_job(job["id"])
        out(f"Removed upkeep job {job['id']}")
    if not args.keep_scripts:
        for name in (REPORT_SCRIPT, MONITOR_SCRIPT):
            path = os.path.join(_home(), "scripts", name)
            if os.path.exists(path):
                os.unlink(path)
                out(f"Removed {path}")
    if not found:
        out("No upkeep job in this profile.")
    return 0


# ------------------------------------------------------------------ argparse wiring


def setup_parser(parser):
    sub = parser.add_subparsers(dest="wiki_hint_cmd")
    up = sub.add_parser("upkeep", help="weekly wiki and memory upkeep cron job")
    upsub = up.add_subparsers(dest="upkeep_cmd")
    ins = upsub.add_parser("install", help="create or update the upkeep job of this profile")
    ins.add_argument("--schedule", help=f"cron expression (default '{DEFAULT_SCHEDULE}')")
    ins.add_argument("--wiki", help="vault path (default: WIKI_PATH)")
    ins.add_argument("--homes", choices=("all", "self"),
                     help="memory files of the whole install or of this profile only "
                          "(default: all from the root profile, self from a named profile)")
    ins.add_argument("--archive-glob", help="pages that are archives by design, e.g. '*-statuslog-*'")
    ins.add_argument("--deliver", help="cron delivery target (default: local)")
    ins.add_argument("--paused", action="store_true", help="create (or set) the job paused")
    ins.add_argument("--dry-run", action="store_true", help="print what would be written, change nothing")
    upsub.add_parser("status", help="show the upkeep job of this profile")
    rem = upsub.add_parser("remove", help="remove the upkeep job (and its wrapper scripts)")
    rem.add_argument("--keep-scripts", action="store_true")
    up.set_defaults(func=handle)
    parser.set_defaults(func=handle)


def handle(args):
    if getattr(args, "wiki_hint_cmd", None) != "upkeep" or not getattr(args, "upkeep_cmd", None):
        print("usage: hermes wiki-hint upkeep {install,status,remove} [--help]")
        return 2
    return {"install": install, "status": status, "remove": remove}[args.upkeep_cmd](args)
