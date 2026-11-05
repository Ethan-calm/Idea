import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import nightshift as ns  # noqa: E402

FAKE = str(HERE / "fake_claude.sh")


def make_repo(queue: str) -> Path:
    d = Path(tempfile.mkdtemp())
    run = lambda *a: subprocess.run(["git", "-C", str(d), *a], check=True, capture_output=True)
    run("init", "-q", "-b", "main")
    run("config", "user.email", "t@example.com")
    run("config", "user.name", "t")
    (d / "queue.md").write_text(queue)
    run("add", ".")
    run("commit", "-q", "-m", "init")
    return d


class ParseTests(unittest.TestCase):
    def test_tags_and_guesses(self):
        tasks = ns.parse_queue(
            "intro text\n- [ ] 결제 모듈 리팩터링\n- [x] already done\n- [ ] fix typo !high\n"
            "- [~] half way #medium\n  - [ ] nested item that is fairly long #big !low\n")
        self.assertEqual([t.text for t in tasks],
                         ["결제 모듈 리팩터링", "already done", "fix typo", "half way", "nested item that is fairly long"])
        self.assertEqual([t.size for t in tasks], [8, 1, 1, 3, 8])
        self.assertEqual(tasks[2].priority, "high")
        self.assertEqual(tasks[4].priority, "low")
        self.assertEqual([t.pending for t in tasks], [True, False, True, True, True])

    def test_set_mark_preserves_rest(self):
        q = "- [ ] a task here\n- [ ] b task here #big\n"
        t = ns.parse_queue(q)[1]
        self.assertEqual(ns.set_mark(q, t, "x"), "- [ ] a task here\n- [x] b task here #big\n")

    def test_slug_is_stable_and_safe(self):
        t = ns.parse_queue("- [ ] 다크모드 Login page!\n")[0]
        self.assertRegex(t.slug, r"^[a-z0-9-]+$")
        self.assertEqual(t.slug, ns.parse_queue("- [ ] 다크모드 Login page!\n")[0].slug)


class PlanTests(unittest.TestCase):
    def test_big_first_then_fill_with_small(self):
        tasks = ns.parse_queue("- [ ] s1 #small\n- [ ] b1 #big\n- [ ] m1 #medium\n- [ ] b2 #big\n- [ ] s2 #small\n")
        chosen, deferred = ns.plan_shift(tasks, budget=12)   # 11 usable
        self.assertEqual([t.text for t in chosen], ["b1", "m1"])
        self.assertEqual({t.text for t in deferred}, {"b2", "s1", "s2"})

    def test_priority_and_resume_win(self):
        tasks = ns.parse_queue("- [ ] big #big\n- [ ] urgent #small !high\n- [~] resume me #medium\n")
        chosen, _ = ns.plan_shift(tasks, budget=5)
        self.assertEqual([t.text for t in chosen], ["resume me", "urgent"])


class ReportTests(unittest.TestCase):
    def test_uses_last_json_block(self):
        r = ns.parse_agent_report('x ```json\n{"status":"done"}\n``` y ```json\n{"status":"blocked","summary":"s","decisions":["q"]}\n```')
        self.assertEqual((r.status, r.summary, r.decisions), ("blocked", "s", ["q"]))

    def test_missing_block_is_partial(self):
        self.assertEqual(ns.parse_agent_report("I did stuff").status, "partial")


class EndToEndTests(unittest.TestCase):
    def test_claude_executor_full_night(self):
        repo = make_repo("- [ ] add dark mode feature to login #medium\n"
                         "- [ ] wire payment BLOCK #small\n"
                         "- [ ] hit the LIMIT here #small !low\n"
                         "- [ ] next shift task #big\n")
        ex = ns.ClaudeExecutor(repo, claude_bin=FAKE, extra_args=[])
        ns.cmd_run(repo, repo / "queue.md", budget=6, executor=ex)

        q = (repo / "queue.md").read_text()
        self.assertIn("- [x] add dark mode", q)
        self.assertIn("- [!] wire payment", q)
        self.assertIn("- [ ] hit the LIMIT", q)       # limit → stays queued
        self.assertIn("- [ ] next shift task", q)     # didn't fit the budget

        branches = subprocess.run(["git", "-C", str(repo), "branch", "--list", "nightshift/*"],
                                  capture_output=True, text=True).stdout
        self.assertEqual(branches.count("nightshift/"), 3)
        dark = ns.parse_queue(q)[0]
        log = subprocess.run(["git", "-C", str(repo), "log", "--oneline", f"nightshift/{dark.slug}"],
                             capture_output=True, text=True).stdout
        self.assertIn("nightshift: wip", log)
        # owner's working tree stays clean apart from the queue
        status = subprocess.run(["git", "-C", str(repo), "status", "--porcelain"], capture_output=True, text=True).stdout
        self.assertEqual(status.strip(), "M queue.md")

        brief = ns.cmd_brief(repo, repo / "queue.md")
        self.assertIn("한도에 1번", brief)
        self.assertIn("Need an API key", brief)
        self.assertIn("✅ 끝남", brief)
        self.assertIn("next shift task", brief)
        self.assertTrue(ns.handoff_path(repo, ns.parse_queue(q)[1]).exists())

    def test_resumes_partial_with_handoff(self):
        repo = make_repo("- [~] keep going on this #small\n")
        t = ns.parse_queue((repo / "queue.md").read_text())[0]
        ns.write_handoff(repo, t, ns.Result("partial", "did half of it"))
        seen = {}

        class Spy:
            def run(self, task, handoff):
                seen["handoff"] = handoff
                return ns.Result("done", "finished")

        ns.cmd_run(repo, repo / "queue.md", budget=3, executor=Spy())
        self.assertIn("did half of it", seen["handoff"])
        self.assertIn("- [x] keep going", (repo / "queue.md").read_text())

    def test_cli_dry_run(self):
        repo = make_repo((HERE.parent / "queue.example.md").read_text())
        out = subprocess.run([sys.executable, str(HERE.parent / "nightshift.py"), "run", "--dry-run",
                              "--repo", str(repo), "--budget", "20"], capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        brief = subprocess.run([sys.executable, str(HERE.parent / "nightshift.py"), "brief", "--repo", str(repo)],
                               capture_output=True, text=True).stdout
        self.assertIn("모닝 브리핑", brief)


if __name__ == "__main__":
    unittest.main()
