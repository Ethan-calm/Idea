import io
import json
import random
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import survivor as sv  # noqa: E402


def quiet(fn, *a):
    with redirect_stdout(io.StringIO()):
        return fn(*a)


class GeneticsTests(unittest.TestCase):
    def test_crossover_takes_genes_from_parents(self):
        rng = random.Random(1)
        a, b = sv.random_genes(rng), sv.random_genes(rng)
        for _ in range(50):
            child = sv.crossover(a, b, rng)
            self.assertTrue(all(child[k] in (a[k], b[k]) for k in sv.GENE_POOL))

    def test_mutation_always_changes_something_valid(self):
        rng = random.Random(2)
        g = sv.random_genes(rng)
        for _ in range(50):
            m = sv.mutate(g, rng, rate=0.0)
            self.assertNotEqual(m, g)
            self.assertTrue(all(m[k] in pool for k, pool in sv.GENE_POOL.items()))


class SeasonTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def test_full_season_dry(self):
        a = quiet(sv.cmd_init, self.root, "할 일 앱", 5, 42)
        self.assertEqual(len(sv.alive(a)), 5)
        for rnd in range(1, 4):
            a = quiet(sv.cmd_round, self.root, sv.DryRunner())
            rdir = self.root / "arena" / f"round-{rnd}"
            gallery = (rdir / "gallery.html").read_text()
            self.assertEqual(gallery.count("<iframe"), 5)
            # blind: no contestant names leak into the gallery or entry folders
            for c in sv.alive(a):
                self.assertNotIn(c["name"], gallery)
            self.assertFalse(list(rdir.glob("*/contestant.json")))
            entries_html = "".join(f.read_text() for f in rdir.glob("*/index.html"))
            for c in sv.alive(a):
                self.assertNotIn(c["name"], entries_html)
            letters = list(a["rounds"][-1]["entries"])
            a = quiet(sv.cmd_vote, self.root, letters)          # A best … E worst
        self.assertEqual(len(sv.alive(a)), 5)                   # one out, one born each round
        self.assertEqual(len(a["contestants"]), 8)
        dead = [c for c in a["contestants"] if not c["alive"]]
        self.assertEqual(sorted(c["eliminated_in"] for c in dead), [1, 2, 3])
        babies = [c for c in a["contestants"] if c["parents"]]
        self.assertEqual(len(babies), 3)
        out = io.StringIO()
        with redirect_stdout(out):
            sv.cmd_status(self.root)
        self.assertIn("3라운드", out.getvalue())

    def test_vote_must_rank_everyone(self):
        quiet(sv.cmd_init, self.root, "x", 3, 1)
        quiet(sv.cmd_round, self.root, sv.DryRunner())
        with self.assertRaises(SystemExit):
            quiet(sv.cmd_vote, self.root, ["A", "B"])
        with self.assertRaises(SystemExit):   # can't start a new round before voting
            quiet(sv.cmd_round, self.root, sv.DryRunner())

    def test_crashed_entry_is_eliminated_even_if_voted_first(self):
        a = quiet(sv.cmd_init, self.root, "x", 3, 7)
        # force one brutalist so the fake claude crashes on it
        a["contestants"][0]["genes"]["style"] = "브루탈리스트"
        for c in a["contestants"][1:]:
            c["genes"]["style"] = "미니멀리스트"
        sv.save(self.root, a)
        a = quiet(sv.cmd_round, self.root, sv.ClaudeRunner(str(HERE / "fake_claude.sh"), extra_args=[]))
        entries = a["rounds"][-1]["entries"]
        crashed = next(l for l, e in entries.items() if not e["ok"])
        self.assertIn("완주 실패", (self.root / "arena/round-1/gallery.html").read_text())
        others = [l for l in entries if l != crashed]
        a = quiet(sv.cmd_vote, self.root, [crashed, *others])
        dead = [c for c in a["contestants"] if not c["alive"]]
        self.assertEqual([c["id"] for c in dead], [entries[crashed]["id"]])


if __name__ == "__main__":
    unittest.main()
