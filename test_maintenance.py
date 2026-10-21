"""Offline tests for maintenance.py (stdlib only): a synthetic vault and Hermes homes in a temp dir."""

import datetime
import hashlib
import importlib.util
import json
import os
import shutil
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_KEYS = ("WIKI_PATH", "WIKI_HINT_INDEX_LINE_MAX", "WIKI_HINT_PAGE_MAX_LINES", "WIKI_HINT_LOG_MAX_ENTRIES",
            "WIKI_HINT_LOG_ROTATION", "WIKI_HINT_ARCHIVE_GLOB", "WIKI_HINT_MAINT_HOMES", "HERMES_HOME")


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, file))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def page(title, tags="[hermes]", body="Text.", extra=""):
    return (f"---\ntitle: {title}\ncreated: 2026-10-01\nupdated: 2026-10-01\ntype: concept\n"
            f"tags: {tags}\n{extra}---\n\n{body}\n")


class Base(unittest.TestCase):
    def setUp(self):
        self.m = load("wiki_maintenance_under_test", "maintenance.py")
        self.tmp = tempfile.mkdtemp(prefix="wiki maint ")
        self.saved = {k: os.environ.pop(k, None) for k in ENV_KEYS}
        self.wiki = os.path.join(self.tmp, "vault")
        for d in ("entities", "concepts", "queries", "raw/articles", "_archive"):
            os.makedirs(os.path.join(self.wiki, d))
        self.write("SCHEMA.md", "# Schema\n\n## Tag Taxonomy\n\n- Dev: `hermes`, `plugin`, `memory`\n\n"
                                "## Index\n\n- Indexregel: `- [[pad]] — kernzin`\n")
        self.write("concepts/alpha.md", page("Alpha", body="See [[concepts/beta]] and [[beta]]. `[[not-a-link]]`"))
        self.write("concepts/beta.md", page("Beta", tags="[hermes, unknown-tag]", body="Back to [[alpha]]."))
        self.write("queries/lost.md", page("Lost", body="Links to [[concepts/gone]]."))
        self.write("queries/topic-statuslog-2026-01-01.md", page("Old log", body="\n".join(["x"] * 260)))
        self.write("entities/big.md", page("Big", body="\n".join(["y"] * 250)))
        self.write("entities/nofm.md", "No frontmatter here.\n")
        self.write("_archive/old.md", "[[nowhere]]\n")
        self.write("index.md", "# Index\n\n"
                               "- [[concepts/alpha]] — Alpha page.\n"
                               "- [[concepts/beta]] — " + "b" * 230 + "\n"
                               "- [[concepts/alpha]] — Alpha again.\n"
                               "- [[entities/big]] — Big page.\n"
                               "- [[entities/nofm]] — No frontmatter.\n"
                               "- [[concepts/missing]] — Points nowhere.\n"
                               "- [[entities/topic]] — Hub; its statuslog is linked from there.\n")
        self.write("entities/topic.md", page("Topic", body="Older entries: [[queries/topic-statuslog-2026-01-01]]."))
        self.write("log.md", "# Log\n\n" + "".join(f"## [2026-09-{d:02d}] update | x\n- y\n\n" for d in (1, 2))
                   + "## [2026-10-03] update | z\n")
        body = "\nRaw text.\n"
        good = hashlib.sha256(body.encode()).hexdigest()
        stripped = hashlib.sha256(body.lstrip("\n").encode()).hexdigest()
        self.write("raw/articles/ok.md", f"---\nsource_url: x\nsha256: {good}\n---\n{body}")
        self.write("raw/articles/ok-stripped.md", f"---\nsource_url: x\nsha256: {stripped}\n---\n{body}")
        self.write("raw/articles/drift.md", f"---\nsha256: {good}\n---\nChanged.\n")
        self.write("raw/articles/odd.md", "---\nsha256: pending\n---\nx\n")

    def tearDown(self):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, rel, text, root=None):
        path = os.path.join(root or self.wiki, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    def wiki_report(self, **env):
        os.environ.update(env)
        return self.m.audit_wiki(self.wiki, today=datetime.date(2026, 10, 21))


class WikiChecks(Base):
    def test_index_checks(self):
        r = self.wiki_report()
        self.assertEqual(r["index_missing_pages"]["items"], ["concepts/missing"])
        self.assertEqual([x["page"] for x in r["index_long_lines"]["items"]], ["concepts/beta"])
        self.assertEqual(r["index_duplicates"]["items"], ["concepts/alpha"])
        not_indexed = {x["page"] for x in r["index_not_indexed"]["items"]}
        self.assertEqual(not_indexed, {"queries/lost", "queries/topic-statuslog-2026-01-01"})

    def test_links_frontmatter_tags_and_size(self):
        r = self.wiki_report()
        broken = {(x["page"], x["target"]) for x in r["broken_links"]["items"]}
        self.assertIn(("queries/lost.md", "concepts/gone"), broken)
        self.assertIn(("index.md", "concepts/missing"), broken)
        self.assertNotIn("not-a-link", {t for _, t in broken})          # inside a code span
        self.assertNotIn("pad", {t for _, t in broken})                 # SCHEMA.md example in backticks
        self.assertFalse(any(p.startswith("_archive") for p, _ in broken))  # archive folder is not checked
        self.assertEqual(r["frontmatter_missing"]["items"], [{"page": "entities/nofm", "fields": [
            "title", "created", "updated", "type", "tags"]}])
        self.assertEqual([t["tag"] for t in r["unknown_tags"]["items"]], ["unknown-tag"])
        self.assertEqual({x["page"] for x in r["pages_over_limit"]["items"]},
                         {"entities/big", "queries/topic-statuslog-2026-01-01"})
        self.assertEqual(r["orphans"]["items"], ["queries/lost"])

    def test_archive_glob_exempts_linked_archive_pages(self):
        r = self.wiki_report(WIKI_HINT_ARCHIVE_GLOB="*-statuslog-*")
        self.assertEqual({x["page"] for x in r["index_not_indexed"]["items"]}, {"queries/lost"})
        self.assertEqual([x["page"] for x in r["pages_over_limit"]["items"]], ["entities/big"])
        self.assertEqual(r["archives"], 1)

    def test_limits_come_from_env(self):
        r = self.wiki_report(WIKI_HINT_INDEX_LINE_MAX="300", WIKI_HINT_PAGE_MAX_LINES="1000")
        self.assertEqual(r["index_long_lines"]["count"], 0)
        self.assertEqual(r["pages_over_limit"]["count"], 0)

    def test_log_rotation(self):
        self.assertFalse(self.wiki_report()["log"]["rotation_due"])
        r = self.wiki_report(WIKI_HINT_LOG_ROTATION="monthly")["log"]
        self.assertTrue(r["rotation_due"])
        self.assertIn("2026-09", r["reason"])
        r = self.wiki_report(WIKI_HINT_LOG_ROTATION="entries", WIKI_HINT_LOG_MAX_ENTRIES="2")["log"]
        self.assertTrue(r["rotation_due"])
        self.assertEqual((r["entries"], r["oldest"], r["newest"]), (3, "2026-09-01", "2026-10-03"))

    def test_raw_hashes_accept_both_body_forms(self):
        raw = self.wiki_report()["raw"]
        self.assertEqual(raw["checked"], 3)
        self.assertEqual([x["file"] for x in raw["drift"]["items"]], [os.path.join("raw", "articles", "drift.md")])
        self.assertEqual(raw["unparsable"]["count"], 1)


class MemoryChecks(Base):
    def setUp(self):
        super().setUp()
        self.home = os.path.join(self.tmp, "hermes")
        self.write("config.yaml", "model:\n  default: x\nmemory:\n  memory_char_limit: 100\n  user_char_limit: 60\n",
                   root=self.home)
        # Home-relative paths are checked against HOME; absolute ones (often on a server) are not.
        self.user_home = tempfile.mkdtemp(prefix="wikimaint")
        self.addCleanup(shutil.rmtree, self.user_home, True)
        self.saved_home = os.environ.get("HOME")
        os.environ["HOME"] = self.user_home
        os.makedirs(os.path.join(self.user_home, "exists"))
        entries = ["Alpha fact about the build server and its deploy key rotation",
                   "Alpha fact about the build server and its deploy key rotation schedule",
                   "Tool lives in ~/exists; old copy in ~/gone-folder/tool, server /etc/nginx/x.conf, "
                   "<p>/scripts/x.py",
                   "Alpha fact about the build server and its deploy key rotation"]  # exact duplicate
        self.write("memories/MEMORY.md", "\n§\n".join(entries), root=self.home)
        self.write("memories/USER.md", "Short.", root=self.home)
        self.write("SOUL.md", "soul text", root=self.home)
        prof = os.path.join(self.home, "profiles", "side")
        self.write("memories/MEMORY.md", "x" * 500, root=prof)
        self.write("config.yaml", "memory:\n  memory_char_limit: 2200\n", root=prof)

    def test_memory_counts_like_hermes_and_reads_limits(self):
        os.environ["HERMES_HOME"] = self.home
        homes = self.m.hermes_homes()
        self.assertEqual([os.path.basename(h) for h in homes], ["hermes", "side"])
        report = {h["home"]: h for h in self.m.audit_memory(homes)}
        mem = {f["file"]: f for f in report["default"]["files"]}
        with open(os.path.join(self.home, "memories", "MEMORY.md"), encoding="utf-8") as f:
            parts = list(dict.fromkeys(x.strip() for x in f.read().split("\n§\n")))
        expected = len("\n§\n".join(parts))
        self.assertEqual(mem["MEMORY.md"]["chars"], expected)
        self.assertEqual(mem["MEMORY.md"]["limit"], 100)
        self.assertEqual(mem["MEMORY.md"]["status"], "over")
        self.assertEqual(mem["USER.md"]["status"], "ok")
        self.assertEqual(mem["MEMORY.md"]["near_duplicates"], [[1, 2]])
        missing = mem["MEMORY.md"]["missing_paths"]
        self.assertEqual(missing, [{"entry": 3, "paths": ["~/gone-folder/tool"]}])
        self.assertEqual(mem["MEMORY.md"]["entries"], 3)  # exact duplicates are dropped, as Hermes does
        self.assertEqual(report["default"]["soul_chars"], len("soul text"))
        side = report["side"]["files"][0]
        self.assertEqual((side["limit"], side["status"]), (2200, "ok"))

    def tearDown(self):
        if self.saved_home is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self.saved_home
        super().tearDown()

    def test_attention_lines_do_not_carry_live_counts(self):
        os.environ["HERMES_HOME"] = self.home
        lines = [x for x in self.m.audit(None)["attention"] if x.startswith("memory.")]
        self.assertTrue(lines)
        self.assertFalse(any("/" in x.split(":", 1)[1] for x in lines), lines)

    def test_a_profile_home_resolves_to_the_whole_install(self):
        os.environ["HERMES_HOME"] = os.path.join(self.home, "profiles", "side")
        self.assertEqual([os.path.basename(h) for h in self.m.hermes_homes()], ["hermes", "side"])
        os.environ["WIKI_HINT_MAINT_HOMES"] = self.home
        self.assertEqual(self.m.hermes_homes(), [self.home])

    def test_full_report_attention_and_cli(self):
        os.environ["HERMES_HOME"] = self.home
        report = self.m.audit(self.wiki, today=datetime.date(2026, 10, 21))
        text = "\n".join(report["attention"])
        self.assertIn("wiki.broken_links", text)
        self.assertIn("memory.default.MEMORY.md", text)
        self.assertIn("near-duplicate", text)
        self.assertNotIn("memory.side", text)
        self.assertIn("Needs attention", self.m.render_text(report))
        json.dumps(report)  # serialisable
        self.assertEqual(self.m.main(["--wiki", self.wiki, "--attention"]), 0)

    def test_nothing_is_written(self):
        os.environ["HERMES_HOME"] = self.home

        def snapshot():
            out = {}
            for root in (self.wiki, self.home):
                for dirpath, _, files in os.walk(root):
                    for name in files:
                        p = os.path.join(dirpath, name)
                        out[p] = (os.path.getmtime(p), os.path.getsize(p))
            return out
        before = snapshot()
        self.m.audit(self.wiki)
        self.assertEqual(before, snapshot())

    def test_no_private_names_in_source(self):
        with open(os.path.join(HERE, "maintenance.py"), encoding="utf-8") as f:
            src = f.read().lower()
        for word in ("/home/", "seppe", "aorus"):
            self.assertNotIn(word, src)


class Command(Base):
    def test_slash_command_reports_this_profile_only_unless_asked(self):
        home = os.path.join(self.tmp, "h")
        self.write("memories/MEMORY.md", "mine", root=home)
        self.write("memories/MEMORY.md", "secret-ish neighbour entry ~/nowhere/x", root=os.path.join(home, "profiles", "other"))
        os.environ["WIKI_PATH"] = self.wiki
        os.environ["HERMES_HOME"] = home
        plugin = load("wiki_hint_cmd_under_test", "__init__.py")
        out = plugin._audit_command("")
        self.assertIn("broken wikilinks", out)
        self.assertIn("Memory [default]", out)
        self.assertNotIn("[other]", out)
        wide = plugin._audit_command("--all")
        self.assertIn("Memory [other]", wide)
        self.assertNotIn("nowhere", wide)  # no entry text from other profiles
        data = json.loads(plugin._audit_command("--json"))
        self.assertIn("wiki.broken_links: 2", data["attention"])
        self.assertEqual([h["home"] for h in data["memory"]], ["default"])


class MemoryEdit(unittest.TestCase):
    def setUp(self):
        self.e = load("memory_edit_under_test", "memory_edit.py")
        self.dir = tempfile.mkdtemp(prefix="memedit")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.path = os.path.join(self.dir, "MEMORY.md")
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("\ufeffone\n§\ntwo long detail\n§\nthree")

    def read(self):
        with open(self.path, encoding="utf-8") as f:
            return f.read()

    def test_apply_edits_by_exact_text_with_backup(self):
        out = self.e.apply(self.path, {"remove": ["three"], "replace": [{"old": "two long detail", "new": "two (wiki)"}],
                                       "add": ["four"], "limit": 500}, today="2026-10-21")
        self.assertTrue(out["changed"])
        self.assertEqual(self.read(), "one\n§\ntwo (wiki)\n§\nfour")
        self.assertTrue(os.path.exists(out["backup"]))
        self.assertTrue(os.path.exists(self.path + ".lock"))

    def test_concurrent_additions_survive(self):
        # Another session appended an entry after the plan was written: it is kept.
        with open(self.path, "a", encoding="utf-8") as f:
            f.write("\n§\nadded meanwhile")
        self.e.apply(self.path, {"remove": ["one"], "limit": 500}, today="2026-10-21")
        self.assertEqual(self.e.read_entries(self.path), ["two long detail", "three", "added meanwhile"])

    def test_refusals_write_nothing(self):
        before = self.read()
        for plan in ({"remove": ["nope"], "limit": 500}, {"replace": [{"old": "one", "new": "a\n§\nb"}], "limit": 500},
                     {"replace": [{"old": "one", "new": "a\n§"}], "limit": 500}, {"add": ["x" * 50], "limit": 40},
                     {"add": ["y"]}, {"remove": ["one", "two long detail", "three"], "limit": 500}):
            with self.assertRaises(ValueError):
                self.e.apply(self.path, plan, today="2026-10-21")
        self.assertEqual(self.read(), before)
        self.assertEqual(self.e.main(["apply", self.path, os.path.join(self.dir, "missing.json")]), 1)

    def test_backups_are_capped(self):
        for n in range(6):
            self.e.apply(self.path, {"add": [f"extra {n}"], "limit": 500}, today="2026-10-21")
        backups = [f for f in os.listdir(self.dir) if ".bak-upkeep-" in f]
        self.assertEqual(len(backups), self.e.KEEP_BACKUPS)

    def test_backup_keeps_file_mode_and_restore_reports_newer_entries(self):
        os.chmod(self.path, 0o600)
        out = self.e.apply(self.path, {"remove": ["three"], "limit": 500}, today="2026-10-21")
        self.assertEqual(os.stat(out["backup"]).st_mode & 0o777, 0o600)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write("\n§\nlive session entry")
        res = self.e.restore(self.path, out["backup"], today="2026-10-21")
        self.assertEqual(self.e.read_entries(self.path), ["one", "two long detail", "three"])
        self.assertEqual(res["not_in_backup"], ["live session entry"])


class FakeJobs:
    """Stand-in for Hermes' cron.jobs (create/update/pause/get/list/remove)."""
    def __init__(self):
        self.jobs, self.n = {}, 0

    def list_jobs(self, include_disabled=False):
        return [dict(j) for j in self.jobs.values()]

    def create_job(self, paused=False, paused_reason=None, **fields):
        self.n += 1
        job = dict(fields, id=f"job{self.n}", enabled=not paused)
        self.jobs[job["id"]] = job
        return job

    def update_job(self, job_id, updates):
        self.jobs[job_id].update(updates)

    def pause_job(self, job_id, reason=None):
        self.jobs[job_id]["enabled"] = False

    def get_job(self, job_id):
        return self.jobs.get(job_id)

    def remove_job(self, job_id):
        return self.jobs.pop(job_id, None) is not None


class UpkeepInstall(Base):
    def setUp(self):
        super().setUp()
        self.u = load("wiki_hint_upkeep_under_test", "upkeep.py")
        self.home = os.path.join(self.tmp, "hermes")
        os.makedirs(os.path.join(self.home, "profiles", "p1", "memories"))
        os.makedirs(os.path.join(self.home, "memories"))
        self.write("config.yaml", "approvals:\n  cron_mode: approve\n", root=self.home)
        os.environ.update(HERMES_HOME=self.home, WIKI_PATH=self.wiki)
        self.fake = FakeJobs()
        self.u._cron = lambda: self.fake
        self.out = []

    def args(self, **kw):
        import argparse
        base = dict(schedule=None, wiki=None, homes=None, archive_glob=None, deliver=None, paused=False,
                    dry_run=False, keep_scripts=False)
        base.update(kw)
        return argparse.Namespace(**base)

    def test_plan_covers_the_install_and_quotes_paths(self):
        p = self.u.plan(self.args(archive_glob="*-statuslog-*"))
        self.assertEqual([os.path.basename(h) for h in p["homes"]], ["hermes", "p1"])
        report = p["scripts"][self.u.REPORT_SCRIPT]
        self.assertTrue(report.rstrip().endswith("maintenance.py --json")
                        or report.rstrip().endswith("maintenance.py' --json"), report)
        self.assertIn(f"export WIKI_PATH='{self.wiki}'", report)  # temp path has a space: quoted
        self.assertIn("WIKI_HINT_ARCHIVE_GLOB='*-statuslog-*'", report)
        self.assertIn("--attention", p["scripts"][self.u.MONITOR_SCRIPT])
        self.assertEqual(p["job"]["skills"], ["llm-wiki"])
        self.assertEqual(p["job"]["workdir"], self.wiki)
        self.assertIn("MAINTENANCE.md", p["job"]["prompt"])
        self.assertIn("memory_edit.py", p["job"]["prompt"])

    def test_a_named_profile_defaults_to_its_own_memory_files(self):
        os.environ["HERMES_HOME"] = os.path.join(self.home, "profiles", "p1")
        self.assertEqual(self.u.plan(self.args())["homes"], [os.path.join(self.home, "profiles", "p1")])

    def test_install_is_idempotent_and_remove_cleans_up(self):
        self.assertEqual(self.u.install(self.args(paused=True), out=self.out.append), 0)
        self.assertEqual(len(self.fake.jobs), 1)
        job = next(iter(self.fake.jobs.values()))
        self.assertFalse(job["enabled"])
        self.assertTrue(os.access(os.path.join(self.home, "scripts", self.u.REPORT_SCRIPT), os.X_OK))
        self.u.install(self.args(schedule="0 6 * * 0"), out=self.out.append)
        self.assertEqual(len(self.fake.jobs), 1)
        self.assertEqual(job["schedule"], "0 6 * * 0")
        self.assertFalse(job["enabled"])  # reinstall keeps the pause; resuming is the operator's call
        self.u.remove(self.args(), out=self.out.append)
        self.assertEqual(self.fake.jobs, {})
        self.assertFalse(os.path.exists(os.path.join(self.home, "scripts", self.u.REPORT_SCRIPT)))

    def test_dry_run_writes_nothing_and_cron_mode_is_flagged(self):
        self.u.install(self.args(dry_run=True), out=self.out.append)
        self.assertEqual(self.fake.jobs, {})
        self.assertFalse(os.path.exists(os.path.join(self.home, "scripts")))
        self.write("config.yaml", "approvals:\n  cron_mode: deny\n", root=self.home)
        self.u.install(self.args(), out=self.out.append)
        self.assertTrue(any("cron_mode is deny" in line for line in self.out))

    def test_the_wrapper_runs(self):
        import subprocess
        self.u.install(self.args(), out=self.out.append)
        done = subprocess.run(["bash", os.path.join(self.home, "scripts", self.u.MONITOR_SCRIPT)],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("wiki.broken_links", done.stdout)

    def test_missing_wiki_is_refused(self):
        with self.assertRaises(SystemExit):
            self.u.plan(self.args(wiki=os.path.join(self.tmp, "nope")))


if __name__ == "__main__":
    unittest.main(verbosity=1)
