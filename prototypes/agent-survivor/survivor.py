#!/usr/bin/env python3
"""Agent Survivor — a reality show where AI agents compete and only your taste decides who lives.

Every contestant is a persona made of *genes* (style, pace, priority, quirk).
Each round all survivors build the same challenge, you vote blind in a gallery,
the last place is eliminated, and the top two breed a newcomer
(crossover + mutation). Over time the population evolves toward your taste.

Standard library only. Usage:
    survivor.py init "할 일 관리 앱을 만들어줘" [--size 5]
    survivor.py round [--dry-run]          # every survivor builds; writes gallery.html
    survivor.py vote C A E B D             # best → worst, letters from the gallery
    survivor.py status
"""
from __future__ import annotations

import argparse
import html
import json
import random
import subprocess
import sys
from pathlib import Path

GENE_POOL = {
    "style": ["미니멀리스트", "맥시멀리스트", "레트로 픽셀", "브루탈리스트", "귀여운 파스텔", "신문 편집"],
    "pace": ["속도광 (1시간 안에 끝낸다는 마음으로)", "장인 (디테일을 끝까지 다듬는다)",
             "실험가 (아무도 안 해본 방식을 시도한다)"],
    "priority": ["접근성", "부드러운 애니메이션", "성능", "유머", "데이터 시각화"],
    "quirk": ["모든 기능에 키보드 단축키", "이스터에그 하나 숨기기", "다크모드 필수", "모바일 우선", "효과음"],
}
GENE_EN = {  # for the prompt; agents follow English instructions a bit more reliably
    "미니멀리스트": "minimalist", "맥시멀리스트": "maximalist", "레트로 픽셀": "retro pixel-art",
    "브루탈리스트": "brutalist", "귀여운 파스텔": "cute pastel", "신문 편집": "editorial newspaper",
}
ADJ = ["용감한", "느긋한", "엉뚱한", "꼼꼼한", "수줍은", "재빠른", "고집센", "반짝이는"]
ANIMAL = ["수달", "너구리", "고슴도치", "펭귄", "카멜레온", "여우", "부엉이", "해달", "판다", "도마뱀"]
ARENA = "arena"

# ------------------------------------------------------------ genetics ----


def random_genes(rng: random.Random) -> dict:
    return {k: rng.choice(v) for k, v in GENE_POOL.items()}


def crossover(a: dict, b: dict, rng: random.Random) -> dict:
    return {k: rng.choice([a[k], b[k]]) for k in GENE_POOL}


def mutate(genes: dict, rng: random.Random, rate: float = 0.25) -> dict:
    out = dict(genes)
    for k, pool in GENE_POOL.items():
        if rng.random() < rate:
            out[k] = rng.choice([g for g in pool if g != out[k]])
    if out == genes:  # always at least one novelty, otherwise it is a clone
        k = rng.choice(list(GENE_POOL))
        out[k] = rng.choice([g for g in GENE_POOL[k] if g != genes[k]])
    return out


def new_name(taken: set[str], rng: random.Random) -> str:
    for _ in range(200):
        n = f"{rng.choice(ADJ)} {rng.choice(ANIMAL)}"
        if n not in taken:
            return n
    return f"{rng.choice(ANIMAL)} {len(taken)}"


def persona_prompt(c: dict) -> str:
    g = c["genes"]
    return (f"You are '{c['name']}', a contestant on a coding reality show.\n"
            f"Your design style: {GENE_EN.get(g['style'], g['style'])} ({g['style']}).\n"
            f"Your working pace: {g['pace']}.\n"
            f"You care most about: {g['priority']}.\n"
            f"Your signature quirk: {g['quirk']}.\n"
            "Lean into your personality — a distinctive entry beats a safe one.")


def build_prompt(challenge: str, c: dict) -> str:
    return (f"{persona_prompt(c)}\n\nChallenge: {challenge}\n\n"
            "Deliver a single self-contained file named index.html in the current directory "
            "(inline CSS and JS, no external requests). It is judged by a human looking at it in an "
            "iframe about 480px wide. Judging is blind: never put your name or persona in the page. "
            "Do not ask questions; just build it.")

# ------------------------------------------------------------- runners ----


class ClaudeRunner:
    def __init__(self, claude_bin="claude", timeout=1800, extra_args=None):
        self.claude_bin, self.timeout = claude_bin, timeout
        self.extra_args = extra_args if extra_args is not None else ["--permission-mode", "acceptEdits"]

    def run(self, prompt: str, workdir: Path) -> tuple[bool, str]:
        try:
            p = subprocess.run([self.claude_bin, "-p", prompt, "--output-format", "json", *self.extra_args],
                               cwd=workdir, capture_output=True, text=True, timeout=self.timeout)
        except subprocess.TimeoutExpired:
            return False, "timeout"
        ok = p.returncode == 0 and (workdir / "index.html").exists()
        return ok, (p.stdout or p.stderr)[-300:]


class DryRunner:
    """Builds a toy entry straight from the genes, so you can play the whole show without tokens."""

    PALETTES = {"미니멀리스트": ("#fafafa", "#111", "#111"), "맥시멀리스트": ("#ff3d7f", "#fff", "#ffe600"),
                "레트로 픽셀": ("#1a1c2c", "#f4f4f4", "#38b764"), "브루탈리스트": ("#fff", "#000", "#0000ff"),
                "귀여운 파스텔": ("#ffe4f2", "#5b3b6b", "#9ad0ff"), "신문 편집": ("#f3efe6", "#1d1d1d", "#b3261e")}

    def run(self, prompt: str, workdir: Path) -> tuple[bool, str]:
        meta = json.loads((workdir / "contestant.json").read_text())
        g = meta["genes"]
        bg, ink, accent = self.PALETTES[g["style"]]
        font = "monospace" if "픽셀" in g["style"] or "브루탈" in g["style"] else (
            "Georgia, serif" if "신문" in g["style"] else "system-ui, sans-serif")
        radius = 18 if "파스텔" in g["style"] else 0 if "브루탈" in g["style"] else 8
        anim = "transition: transform .2s; " if "애니메이션" in g["priority"] else ""
        items = ["아침 산책", "코드 리뷰", "물 마시기"]
        lis = "".join(f'<li style="border:2px solid {accent};border-radius:{radius}px;padding:10px;margin:8px 0;{anim}"'
                      f' onmouseover="this.style.transform=\'scale(1.03)\'" onmouseout="this.style.transform=\'\'">'
                      f'<input type="checkbox"> {i}</li>' for i in items)
        egg = '<p style="opacity:.05" title="🥚">you found me</p>' if "이스터에그" in g["quirk"] else ""
        (workdir / "index.html").write_text(
            f'<!doctype html><meta charset="utf-8"><body style="margin:0;padding:24px;background:{bg};color:{ink};'
            f'font-family:{font}"><h1 style="color:{accent};margin-top:0">할 일 ✦</h1>'
            f'<p style="opacity:.7">{html.escape(g["priority"])} · {html.escape(g["quirk"])}</p>'
            f'<ul style="list-style:none;padding:0">{lis}</ul>{egg}</body>')
        return True, "dry run"

# --------------------------------------------------------------- arena ----


def arena_path(root: Path) -> Path:
    return root / ARENA / "arena.json"


def load(root: Path) -> dict:
    p = arena_path(root)
    if not p.exists():
        sys.exit("no arena yet — run: survivor.py init \"<challenge>\"")
    return json.loads(p.read_text())


def save(root: Path, a: dict) -> None:
    arena_path(root).parent.mkdir(parents=True, exist_ok=True)
    arena_path(root).write_text(json.dumps(a, ensure_ascii=False, indent=2))


def alive(a: dict) -> list[dict]:
    return [c for c in a["contestants"] if c["alive"]]


def make_contestant(a: dict, genes: dict, rng: random.Random, parents=None) -> dict:
    c = {"id": f"c{len(a['contestants']) + 1}", "name": new_name({x["name"] for x in a["contestants"]}, rng),
         "genes": genes, "parents": parents or [], "born": a["round"], "alive": True,
         "points": 0, "placings": [], "eliminated_in": None}
    a["contestants"].append(c)
    return c


def cmd_init(root: Path, challenge: str, size: int, seed: int | None) -> dict:
    if arena_path(root).exists():
        sys.exit("arena already exists — delete the arena/ folder to start a new season")
    seed = seed if seed is not None else random.randrange(1 << 30)
    a = {"challenge": challenge, "seed": seed, "round": 0, "contestants": [], "rounds": []}
    rng = random.Random(seed)
    for _ in range(size):
        make_contestant(a, random_genes(rng), rng)
    save(root, a)
    print(f"🏝️  시즌 시작! 과제: {challenge}\n")
    for c in alive(a):
        print(f"  {c['name']:<10}  {' / '.join(c['genes'].values())}")
    return a


def cmd_round(root: Path, runner) -> dict:
    a = load(root)
    if a["rounds"] and a["rounds"][-1]["ranking"] is None:
        sys.exit(f"round {a['round']} is still waiting for your vote — open its gallery.html")
    a["round"] += 1
    n = a["round"]
    rdir = root / ARENA / f"round-{n}"
    rng = random.Random(a["seed"] * 1000 + n)
    survivors = alive(a)
    letters = [chr(ord("A") + i) for i in range(len(survivors))]
    order = survivors[:]
    rng.shuffle(order)  # blind: letters don't reveal who is who
    entries = {}
    for letter, c in zip(letters, order):
        d = rdir / letter
        d.mkdir(parents=True, exist_ok=True)
        (d / "contestant.json").write_text(json.dumps(c, ensure_ascii=False))
        print(f"🎬 {letter} 작업 중…", flush=True)
        ok, note = runner.run(build_prompt(a["challenge"], c), d)
        entries[letter] = {"id": c["id"], "ok": ok, "note": note}
        (d / "contestant.json").unlink()  # keep the gallery folder blind
    a["rounds"].append({"n": n, "entries": entries, "ranking": None})
    save(root, a)
    write_gallery(rdir, n, a["challenge"], entries)
    print(f"\n🗳️  갤러리: {rdir / 'gallery.html'}")
    return a


def write_gallery(rdir: Path, n: int, challenge: str, entries: dict) -> None:
    cards = "".join(
        f'<figure data-l="{l}"><figcaption><b>{l}</b><button onclick="pick(\'{l}\')">이걸로 ▶</button></figcaption>'
        + (f'<iframe sandbox="allow-scripts" src="{l}/index.html" loading="lazy"></iframe>' if e["ok"]
           else '<div class="dnf">💥 완주 실패</div>') + "</figure>"
        for l, e in entries.items())
    (rdir / "gallery.html").write_text(f"""<!doctype html><html lang="ko"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Agent Survivor · Round {n}</title>
<style>
:root{{--bg:#f4f3ef;--ink:#1b1b1f;--card:#fff;--line:#ddd9d0;--accent:#e4572e}}
@media (prefers-color-scheme:dark){{:root{{--bg:#121214;--ink:#eee;--card:#1d1d21;--line:#333;--accent:#ff7a50}}}}
body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,"Noto Sans KR",sans-serif}}
header{{padding:20px 16px 0;max-width:1400px;margin:auto}} h1{{margin:0;font-size:22px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,420px),1fr));gap:16px;padding:16px;max-width:1400px;margin:auto}}
figure{{margin:0;background:var(--card);border:2px solid var(--line);border-radius:14px;overflow:hidden}}
figure.picked{{opacity:.35;border-style:dashed}}
figcaption{{display:flex;justify-content:space-between;align-items:center;padding:8px 12px;border-bottom:1px solid var(--line)}}
figcaption b{{font-size:20px}} iframe{{width:100%;height:360px;border:0;background:#fff;display:block}}
.dnf{{height:360px;display:grid;place-items:center;font-size:20px}}
button{{font:inherit;border:1px solid var(--line);background:var(--bg);color:var(--ink);border-radius:999px;padding:4px 12px;cursor:pointer}}
.bar{{position:sticky;bottom:0;background:var(--card);border-top:1px solid var(--line);padding:12px 16px;display:flex;gap:12px;align-items:center;flex-wrap:wrap}}
code{{background:var(--bg);padding:6px 10px;border-radius:8px;font-size:14px}}
</style>
<header><h1>🏝️ Round {n} — 블라인드 투표</h1><p>과제: {html.escape(challenge)}<br>
마음에 드는 순서대로 <b>이걸로 ▶</b>를 누르세요. 누가 만들었는지는 투표 후에 공개됩니다.</p></header>
<div class="grid">{cards}</div>
<div class="bar"><span>순위:</span><code id="cmd">아직 고르지 않았어요</code>
<button onclick="copy()">복사</button><button onclick="reset()">다시</button></div>
<script>
const all={json.dumps(list(entries))};let rank=[];
function render(){{document.querySelectorAll('figure').forEach(f=>f.classList.toggle('picked',rank.includes(f.dataset.l)));
const left=all.filter(l=>!rank.includes(l));const full=left.length<=1?rank.concat(left):rank;
document.getElementById('cmd').textContent=rank.length?(left.length<=1?'python3 survivor.py vote '+full.join(' '):rank.join(' > ')+' …'):'아직 고르지 않았어요';}}
function pick(l){{if(!rank.includes(l))rank.push(l);render();}}
function reset(){{rank=[];render();}}
function copy(){{navigator.clipboard&&navigator.clipboard.writeText(document.getElementById('cmd').textContent);}}
</script></html>""")


def cmd_vote(root: Path, letters: list[str]) -> dict:
    a = load(root)
    if not a["rounds"] or a["rounds"][-1]["ranking"] is not None:
        sys.exit("nothing to vote on — run: survivor.py round")
    r = a["rounds"][-1]
    letters = [l.upper() for l in letters]
    if sorted(letters) != sorted(r["entries"]):
        sys.exit(f"rank every entry exactly once: {' '.join(sorted(r['entries']))}")
    r["ranking"] = letters
    by_id = {c["id"]: c for c in a["contestants"]}
    ranked = [by_id[r["entries"][l]["id"]] for l in letters]
    # entries that crashed always sink to the bottom, whatever the vote said
    ranked.sort(key=lambda c: not next(e["ok"] for e in r["entries"].values() if e["id"] == c["id"]))
    n = len(ranked)
    print("🎭 정체 공개!\n")
    for place, c in enumerate(ranked, 1):
        c["points"] += n - place
        c["placings"].append(place)
        letter = next(l for l, e in r["entries"].items() if e["id"] == c["id"])
        print(f"  {place}위  {letter} = {c['name']}  ({c['genes']['style']}, {c['genes']['pace'].split(' ')[0]})")
    rng = random.Random(a["seed"] * 7919 + a["round"])
    if n > 2:
        loser = ranked[-1]
        loser["alive"], loser["eliminated_in"] = False, a["round"]
        print(f"\n💀 탈락: {loser['name']}")
    p1, p2 = ranked[0], ranked[1] if n > 1 else ranked[0]
    baby = make_contestant(a, mutate(crossover(p1["genes"], p2["genes"], rng), rng), rng, [p1["id"], p2["id"]])
    print(f"🐣 새 참가자: {baby['name']} ← {p1['name']} × {p2['name']}\n   {' / '.join(baby['genes'].values())}")
    save(root, a)
    return a


def taste_summary(a: dict) -> list[str]:
    """Which genes win? Average points per gene value over every placing so far."""
    score: dict[tuple, list[int]] = {}
    for c in a["contestants"]:
        for place in c["placings"]:
            for k, v in c["genes"].items():
                score.setdefault((k, v), []).append(place)
    seen_twice = [kv for kv in score.items() if len(kv[1]) >= 2]  # one placing is luck, not taste
    ranked = sorted(seen_twice, key=lambda kv: sum(kv[1]) / len(kv[1]))
    return [f"{v} (평균 {sum(p) / len(p):.1f}위)" for (k, v), p in ranked[:3]]


def cmd_status(root: Path) -> None:
    a = load(root)
    print(f"🏝️  {a['challenge']} — {a['round']}라운드 진행\n")
    for c in sorted(a["contestants"], key=lambda c: (not c["alive"], -c["points"])):
        mark = "🟢" if c["alive"] else f"💀R{c['eliminated_in']}"
        hist = "".join(str(p) for p in c["placings"]) or "-"
        print(f"  {mark:<5} {c['name']:<10} {c['points']:>3}점  순위기록 {hist:<6} {' / '.join(c['genes'].values())}")
    t = taste_summary(a)
    if t:
        print("\n🎯 당신의 취향을 이기고 있는 유전자: " + ", ".join(t))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init"); p.add_argument("challenge"); p.add_argument("--size", type=int, default=5)
    p.add_argument("--seed", type=int)
    p = sub.add_parser("round"); p.add_argument("--dry-run", action="store_true")
    p.add_argument("--claude-bin", default="claude"); p.add_argument("--timeout", type=int, default=1800)
    p = sub.add_parser("vote"); p.add_argument("letters", nargs="+")
    sub.add_parser("status")
    ap.add_argument("--root", default=".", help="where the arena/ folder lives")
    a = ap.parse_args(argv)
    root = Path(a.root).resolve()
    if a.cmd == "init":
        cmd_init(root, a.challenge, a.size, a.seed)
    elif a.cmd == "round":
        cmd_round(root, DryRunner() if a.dry_run else ClaudeRunner(a.claude_bin, a.timeout))
    elif a.cmd == "vote":
        cmd_vote(root, a.letters)
    else:
        cmd_status(root)
    return 0


if __name__ == "__main__":
    sys.exit(main())
