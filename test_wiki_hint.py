"""Offline tests for the wiki-hint plugin (stdlib only, no network).

Run with either:
    python3 test_wiki_hint.py
    python3 -m pytest test_wiki_hint.py

All tests use a synthetic wiki in a temp dir, so they pass on any machine and
never touch a real wiki. Assertions avoid the injected wording (which differs
per copy) and check markers, paths and page slugs instead.
"""

import importlib.util
import os
import shutil
import tempfile
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))

INDEX = """# Wiki Index

## Entities
- [[entities/workstation]] - Local workstation and agent home base.
- [[entities/newmotion-laadpaal-rozenlaan]] - NewMotion laadpaal at home; freed from vendor lock via IP bypass.
- [[entities/polymarket-btc5m-bots]] - Polymarket btc5m research bot; fail-closed execution.

## Concepts
- [[concepts/opzetzwembad]] - Above-ground pool: zandfilterpomp, slangen and liner choices.
- [[concepts/cloudflare-redirect-rules]] - Cloudflare redirect rules via the API.
- [[concepts/cloudflare-redirect-rules]] - Cloudflare redirect rules via the API.
- [[concepts/workstation-home-config-backup]] - Encrypted backup of workstation home config.
- [[concepts/turso-selfhost]] - Turso/libSQL self-host and free tier.
- [[concepts/coolify-self-hosted-runners]] - Coolify with self-hosted GitHub runners on the workstation.

## Queries
- [[queries/polymarket-btc5m-stop-loss-review]] - Stop-loss hold review for the btc5m bot.
- [[queries/workstation-gpu-mining-tuning]] - Workstation GPU mining tuning, start scripts.
- [[queries/workstation-nvme-failure]] - Workstation NVMe controller failure.
- [[queries/workstation-ram-upgrade]] - Workstation RAM upgrade options.
- [[queries/workstation-psu-rma]] - Workstation PSU RMA.
"""


def load_plugin():
    spec = importlib.util.spec_from_file_location(
        "wiki_hint_under_test", os.path.join(HERE, "__init__.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def slugs(lines):
    out = []
    for line in lines:
        start = line.index("[[") + 2
        out.append(line[start:line.index("]]", start)])
    return out


class Base(unittest.TestCase):
    def setUp(self):
        self.m = load_plugin()
        self.tmp = tempfile.mkdtemp(prefix="wiki hint ")  # space on purpose
        self.wiki = os.path.join(self.tmp, "My Wiki")
        os.makedirs(self.wiki)
        with open(os.path.join(self.wiki, "index.md"), "w", encoding="utf-8") as f:
            f.write(INDEX)
        self.m.WIKI_FALLBACK = os.path.join(self.tmp, "missing-fallback")
        self._saved = {k: os.environ.pop(k, None) for k in
                       ("WIKI_PATH", "WIKI_HINT_LANG", "WIKI_HINT_NUDGE_THRESHOLD", "WIKI_HINT_SKIP_SOURCES",
                        "HERMES_SESSION_SOURCE")}
        os.environ["WIKI_PATH"] = self.wiki

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def call(self, msg, first=False, sid="s1", **kw):
        r = self.m._pre_llm_call(msg, is_first_turn=first, session_id=sid, **kw)
        return r["context"] if r else ""

    def matches(self, msg):
        return slugs(self.m._best_matches(msg, self.m._index_entries(self.wiki)))


class Matching(Base):
    def test_two_terms_match_slug(self):
        self.assertIn("entities/newmotion-laadpaal-rozenlaan",
                      self.matches("De newmotion laadpaal doet raar"))

    def test_single_shared_word_is_not_enough(self):
        # one shared word, even a rare one that is in the slug, is a coincidence
        self.assertEqual(self.matches("De laadpaal doet raar"), [])
        self.assertEqual(self.matches("Welke slangen?"), [])

    def test_compound_and_inflection(self):
        # filterpomp inside zandfilterpomp (compound), opzetzwembaden (inflection)
        self.assertIn("concepts/opzetzwembad",
                      self.matches("Welke filterpomp voor het opzetzwembad?"))
        self.assertIn("concepts/opzetzwembad",
                      self.matches("Welke liner voor opzetzwembaden?"))

    def test_multi_term(self):
        got = self.matches("Polymarket btc5m bot stop-loss herzien")
        self.assertIn("queries/polymarket-btc5m-stop-loss-review", got)

    def test_trivial_question_injects_nothing(self):
        self.assertEqual(self.matches("Wat is de hoofdstad van Frankrijk?"), [])

    def test_duplicate_index_lines_are_deduplicated(self):
        got = self.matches("Cloudflare redirect rule instellen")
        self.assertEqual(got.count("concepts/cloudflare-redirect-rules"), 1)

    def test_common_term_cannot_carry_a_match(self):
        # "workstation" appears in many pages; with a common-term floor of 2
        # it is generic and "start" (summary only) must not pull in the mining page.
        self.m.COMMON_DF_MIN = 2
        got = self.matches("Backup service start niet op de workstation")
        self.assertNotIn("queries/workstation-gpu-mining-tuning", got)
        # generic "workstation" plus one specific term is still one specific term
        self.assertEqual(got, [])
        got = self.matches("Encrypted backup van de workstation config start niet")
        self.assertEqual(got[0], "concepts/workstation-home-config-backup")
        self.assertNotIn("queries/workstation-gpu-mining-tuning", got)

    def test_hyphenated_query_word_not_double_counted(self):
        # Only the double count ("self-host" + "self") could give Coolify a
        # second specific term.
        got = self.matches("Is Turso free to self-host?")
        self.assertEqual(got[0], "concepts/turso-selfhost")
        self.assertNotIn("concepts/coolify-self-hosted-runners", got)

    def test_max_three_lines_and_line_cap(self):
        for line in self.m._best_matches(
                "workstation nvme ram psu gpu mining backup config upgrade failure",
                self.m._index_entries(self.wiki)):
            self.assertLessEqual(len(line), self.m.MAX_LINE_CHARS)
        self.assertLessEqual(len(self.matches(
            "workstation nvme ram psu gpu mining backup config upgrade failure")), 3)


class Multimodal(Base):
    def test_list_of_parts_is_flattened(self):
        msg = [{"type": "text", "text": "De newmotion laadpaal doet raar"},
               {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]
        self.assertIn("newmotion-laadpaal-rozenlaan", self.call(msg))

    def test_image_description_is_not_matched(self):
        # cli and gateway prepend a vision description; only the user's own words count
        cli = ("[The user attached an image. Here's what it contains:\n"
               "A Polymarket btc5m bot dashboard with a stop-loss review panel.]\n"
               "[If you need a closer look, use vision_analyze with image_url: /tmp/x.png]\n\n")
        gw = ("[The user sent an image~ Here's what I can see:\n"
              "A Polymarket btc5m bot dashboard with a stop-loss review panel.]\n"
              "[If you need a closer look, use vision_analyze with image_url: /tmp/x.png ~]\n\n")
        gw_fail = ("[The user sent an image but I couldn't quite see it this time (>_<) You can try looking "
                   "at it yourself with vision_analyze using image_url: /tmp/polymarket-btc5m.png]\n\n")
        for prefix in (cli, gw, gw_fail):
            self.assertNotIn("polymarket", self.call(prefix + "Kijk hier eens naar", sid=prefix))
            self.assertIn("newmotion-laadpaal",
                          self.call(prefix + "De newmotion laadpaal doet raar", sid=prefix + "2"))

    def test_image_description_edge_cases(self):
        tail = "De newmotion laadpaal doet raar"
        cases = (
            # description with "]" inside (code, markdown)
            "[The user attached an image. Here's what it contains:\nPolymarket code: a[0] = btc5m]\n"
            "[If you need a closer look, use vision_analyze with image_url: /tmp/x.png]\n\n",
            # tui format
            "[The user attached an image: polymarket-btc5m.png]\n"
            "[Examine it with the vision_analyze tool using image_url: /tmp/polymarket-btc5m.png]\n\n",
            # failure note with multi-line exception text containing "]", then a success block
            "[The user attached an image but analysis failed (polymarket\nbtc5m [rate] limit). You can try "
            "examining it with vision_analyze using image_url: /tmp/a.png]\n\n"
            "[The user attached an image. Here's what it contains:\nPolymarket btc5m bot]\n"
            "[If you need a closer look, use vision_analyze with image_url: /tmp/b.png]\n\n",
        )
        for i, prefix in enumerate(cases):
            stripped = self.m._strip_attachments(prefix + tail)
            self.assertNotIn("btc5m", stripped)
            self.assertIn(tail, stripped)
            out = self.call(prefix + tail, sid=f"edge{i}")
            self.assertIn("newmotion-laadpaal", out)
            self.assertNotIn("polymarket", out)

    def test_odd_inputs_never_raise(self):
        for msg in (None, 42, {"x": 1}, [None, {"text": None}, {"text": 5}], b"bytes", ""):
            self.call(msg, first=True)


class Cron(Base):
    def test_real_core_session_id_format(self):
        # cron/scheduler.py: f"cron_{job_id}_{YYYYmmdd_HHMMSS}" and platform="cron"
        ctx = self.call("Run the job", first=True, sid="cron_83a754213aef_20261011_004500")
        self.assertEqual(ctx.count("[wiki-hint]"), 2)  # reminder + cron instruction

    def test_platform_cron_after_session_rotation(self):
        ctx = self.call("Run the job", first=True, sid="20261011_004501_ab12cd", platform="cron")
        self.assertEqual(ctx.count("[wiki-hint]"), 2)

    def test_not_cron(self):
        self.assertEqual(self.call("Hallo", first=True, sid="20261011_x", platform="telegram")
                         .count("[wiki-hint]"), 1)
        self.assertFalse(self.m._is_cron("my_cron_job", "cli"))

    def test_cron_instruction_only_on_first_turn(self):
        self.assertEqual(self.call("Hallo", first=False, sid="cron_x_1", platform="cron"), "")


class Nudge(Base):
    def research(self, n, sid="s1", tool="web_search"):
        for _ in range(n):
            self.m._post_tool_call(tool, args={}, session_id=sid)

    def nudged(self, sid="s1"):
        return self.call("Dank je", sid=sid).count("[wiki-hint]") == 1

    def test_threshold(self):
        self.research(self.m._nudge_threshold() - 1)
        self.assertFalse(self.nudged())
        self.research(1)
        self.assertTrue(self.nudged())
        self.assertFalse(self.nudged())  # one-shot

    def test_browser_steps_count_for_a_quarter(self):
        # clicking through a page while testing it is not research
        self.research(self.m._nudge_threshold() * 4 - 1, tool="browser_exec")
        self.assertFalse(self.nudged())
        self.research(1, tool="browser_exec")
        self.assertTrue(self.nudged())
        # mixed: weights add up, one nudge per threshold crossed
        self.research(self.m._nudge_threshold() - 1, sid="m")
        self.research(4, sid="m", tool="browser_exec")
        self.assertTrue(self.nudged(sid="m"))
        self.assertFalse(self.nudged(sid="m"))

    def test_non_research_tools_ignored(self):
        self.research(self.m._nudge_threshold() * 2, tool="terminal")
        self.assertFalse(self.nudged())

    def test_limit_per_session(self):
        got = 0
        for _ in range(self.m.MAX_NUDGES_PER_SESSION + 3):
            self.research(self.m._nudge_threshold())
            got += self.nudged()
        self.assertEqual(got, self.m.MAX_NUDGES_PER_SESSION)

    def test_sessions_are_isolated(self):
        self.research(self.m._nudge_threshold(), sid="a")
        self.assertFalse(self.nudged(sid="b"))
        self.assertTrue(self.nudged(sid="a"))

    def test_thread_safety(self):
        n_threads, per = 8, 50
        ts = [threading.Thread(target=self.research, args=(per, "t")) for _ in range(n_threads)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(self.m._session_state["t"]["count"], n_threads * per)


class Dedup(Base):
    def test_same_page_not_repeated_within_session(self):
        self.assertIn("newmotion-laadpaal", self.call("De newmotion laadpaal doet raar"))
        self.assertNotIn("newmotion-laadpaal", self.call("En die newmotion laadpaal nu?"))
        self.assertIn("newmotion-laadpaal", self.call("De newmotion laadpaal doet raar", sid="other"))


class State(Base):
    def test_state_is_bounded(self):
        cap = self.m.MAX_TRACKED_SESSIONS
        for i in range(cap * 3):
            self.m._post_tool_call("web_search", session_id=f"cron_{i}")
            self.call("De newmotion laadpaal doet raar", sid=f"cron_{i}")
        self.assertLessEqual(len(self.m._session_state), cap)

    def test_plain_turns_create_no_state(self):
        for i in range(100):
            self.call("Hallo", first=True, sid=f"x{i}")
        self.assertEqual(len(self.m._session_state), 0)


class Robustness(Base):
    def test_missing_index_still_reminds(self):
        os.remove(os.path.join(self.wiki, "index.md"))
        self.assertEqual(self.call("De laadpaal", first=True).count("[wiki-hint]"), 1)

    def test_empty_index(self):
        open(os.path.join(self.wiki, "index.md"), "w").close()
        self.assertEqual(self.call("De newmotion laadpaal doet raar"), "")

    def test_invalid_utf8_index(self):
        with open(os.path.join(self.wiki, "index.md"), "wb") as f:
            f.write(b"- [[concepts/laadpaal-kapot]] \xff\xfe laadpaal\n")
        ctx = self.call("laadpaal kapot", first=True, sid="cron_j_1")
        self.assertEqual(ctx.count("[wiki-hint]"), 3)

    def test_index_is_a_directory(self):
        os.remove(os.path.join(self.wiki, "index.md"))
        os.makedirs(os.path.join(self.wiki, "index.md"))
        self.assertEqual(self.call("De laadpaal", first=True).count("[wiki-hint]"), 1)

    def test_no_wiki_at_all(self):
        os.environ["WIKI_PATH"] = os.path.join(self.tmp, "does not exist")
        self.assertIsNone(self.m._pre_llm_call("x", is_first_turn=True, session_id="s"))

    def test_wiki_path_with_spaces_quotes_and_tilde(self):
        self.assertEqual(self.m._wiki_path(), self.wiki)
        os.environ["WIKI_PATH"] = f'"{self.wiki}"'
        self.assertEqual(self.m._wiki_path(), self.wiki)
        home = os.path.expanduser("~")
        if self.wiki.startswith(home + os.sep):
            os.environ["WIKI_PATH"] = "~" + self.wiki[len(home):]
            self.assertEqual(self.m._wiki_path(), self.wiki)

    def test_index_reload_on_change(self):
        self.assertEqual(self.matches("zeppelin hangar"), [])
        p = os.path.join(self.wiki, "index.md")
        with open(p, "a", encoding="utf-8") as f:
            f.write("- [[concepts/zeppelin-hangar]] - Zeppelin hangar notes.\n")
        st = os.stat(p)
        os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))
        self.assertIn("concepts/zeppelin-hangar", self.matches("zeppelin hangar"))

    def test_hint_never_writes_to_wiki(self):
        before = sorted(os.listdir(self.wiki))
        self.m._post_tool_call("web_search", session_id="s")
        self.call("De newmotion laadpaal doet raar", first=True, sid="cron_x_1", platform="cron")
        self.assertEqual(sorted(os.listdir(self.wiki)), before)

    def test_register(self):
        hooks = {}

        class Ctx:
            def register_hook(self, name, fn):
                hooks[name] = fn

        self.m.register(Ctx())
        self.assertEqual(set(hooks), {"pre_llm_call", "post_tool_call"})

    def test_lang_defaults_to_english_and_switches_to_dutch(self):
        en = self.call("Hallo", first=True)
        self.assertIn("keeps an llm-wiki", en)
        os.environ["WIKI_HINT_LANG"] = "nl"
        self.assertIn("Er is een llm-wiki", self.call("Hallo", first=True))
        os.environ["WIKI_HINT_LANG"] = "xx"  # unknown -> default
        self.assertIn("keeps an llm-wiki", self.call("Hallo", first=True))

    def test_nudge_threshold_from_env(self):
        self.assertEqual(self.m._nudge_threshold(), 3)
        os.environ["WIKI_HINT_NUDGE_THRESHOLD"] = "5"
        self.assertEqual(self.m._nudge_threshold(), 5)
        for bad in ("0", "-2", "abc", ""):
            os.environ["WIKI_HINT_NUDGE_THRESHOLD"] = bad
            self.assertEqual(self.m._nudge_threshold(), 3)

    def test_subagents_and_tool_runs_get_nothing(self):
        for platform in ("subagent", "tool"):
            self.assertEqual(self.call("De newmotion laadpaal doet raar", first=True, platform=platform), "")
        os.environ["HERMES_SESSION_SOURCE"] = "tool"  # hermes chat --source tool
        self.assertEqual(self.call("De newmotion laadpaal doet raar", first=True, platform="cli"), "")
        os.environ.pop("HERMES_SESSION_SOURCE")
        self.assertIn("newmotion-laadpaal", self.call("De newmotion laadpaal doet raar", first=True, platform="cli"))

    def test_skip_sources_can_be_changed_or_emptied(self):
        os.environ["WIKI_HINT_SKIP_SOURCES"] = ""
        self.assertIn("newmotion-laadpaal", self.call("De newmotion laadpaal doet raar", platform="subagent"))
        os.environ["WIKI_HINT_SKIP_SOURCES"] = "telegram, cron"
        self.assertEqual(self.call("De newmotion laadpaal doet raar", sid="s2", platform="telegram"), "")
        self.assertIn("newmotion-laadpaal", self.call("De newmotion laadpaal doet raar", sid="s3", platform="subagent"))

    def test_system_notes_get_no_page_suggestions(self):
        for note in ("[IMPORTANT: Background process proc_1 completed. laadpaal",
                     "[CONTEXT COMPACTION — REFERENCE ONLY] laadpaal",
                     "  [SYSTEM: laadpaal"):
            self.assertNotIn("newmotion-laadpaal", self.call(note, sid=note))
        # the cron wrapper message keeps the reminder and the cron instruction
        out = self.call("[IMPORTANT: You are running as a scheduled cron job. laadpaal", first=True,
                        sid="cron_abc_20261017_000000")
        self.assertIn("[wiki-hint]", out)
        self.assertNotIn("newmotion-laadpaal", out)
        self.assertEqual(out.count("[wiki-hint]"), 2)

    def test_no_private_paths_in_source(self):
        with open(os.path.join(HERE, "__init__.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertNotIn("/home/", src)
        self.assertEqual(self.m.WIKI_FALLBACK, os.path.join(self.tmp, "missing-fallback"))


if __name__ == "__main__":
    unittest.main(verbosity=1)
