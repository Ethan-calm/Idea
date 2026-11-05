#!/usr/bin/env python3
"""Night Shift — an agent that works while you sleep, scheduled around usage-limit resets.

Write tasks in queue.md before bed, then let cron (or a scheduled task) call
`nightshift.py run` at every limit reset. Each run is one *shift*: it plans the
work that fits the budget, runs each task on its own git branch, leaves a
handoff note for the next shift, and `nightshift.py brief` writes the morning
report.

Standard library only. Usage:
    nightshift.py plan  [--budget N]
    nightshift.py run   [--budget N] [--executor claude|dry] [--dry-run]
    nightshift.py brief [--out morning.md]
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path

STATE_DIR = ".nightshift"
SIZES = {"small": 1, "medium": 3, "big": 8}
PRIORITIES = {"high": 0, "normal": 1, "low": 2}
BIG_WORDS = ("리팩터", "refactor", "migrate", "마이그레이션", "redesign", "재설계", "research", "리서치",
             "구현", "implement", "새 기능", "feature", "아키텍처", "architecture")
SMALL_WORDS = ("오타", "typo", "rename", "이름 변경", "lint", "린트", "format", "readme", "주석", "comment", "bump")
LIMIT_PATTERNS = re.compile(r"usage limit|rate limit|limit reached|quota|429|overloaded", re.I)

# ---------------------------------------------------------------- queue ----

TASK_RE = re.compile(r"^(?P<indent>\s*)- \[(?P<mark>[ xX~!])\]\s+(?P<text>.+?)\s*$")


@dataclass
class Task:
    line_no: int
    text: str                 # text with tags stripped
    raw: str                  # original line text after the checkbox
    mark: str = " "           # ' ' todo, 'x' done, '~' partial, '!' blocked
    size: int = 3
    priority: str = "normal"
    sized_by: str = "guess"   # 'tag' or 'guess'

    @property
    def slug(self) -> str:
        ascii_part = re.sub(r"[^a-z0-9]+", "-", self.text.lower()).strip("-")[:30].strip("-")
        digest = hashlib.sha1(self.text.encode()).hexdigest()[:6]
        return f"{ascii_part}-{digest}" if ascii_part else digest

    @property
    def pending(self) -> bool:
        return self.mark in (" ", "~")


def estimate_size(text: str) -> int:
    low = text.lower()
    if any(w in low for w in BIG_WORDS):
        return SIZES["big"]
    if any(w in low for w in SMALL_WORDS) or len(text) < 20:
        return SIZES["small"]
    return SIZES["medium"]


def parse_queue(content: str) -> list[Task]:
    tasks = []
    for i, line in enumerate(content.splitlines()):
        m = TASK_RE.match(line)
        if not m:
            continue
        raw = m.group("text")
        size_tag = re.search(r"#(small|medium|big)\b", raw)
        prio_tag = re.search(r"!(high|low)\b", raw)
        text = re.sub(r"\s*(#(small|medium|big)|!(high|low))\b", "", raw).strip()
        t = Task(line_no=i, text=text, raw=raw, mark=m.group("mark").lower())
        if size_tag:
            t.size, t.sized_by = SIZES[size_tag.group(1)], "tag"
        else:
            t.size = estimate_size(text)
        if prio_tag:
            t.priority = prio_tag.group(1)
        tasks.append(t)
    return tasks


def set_mark(content: str, task: Task, mark: str) -> str:
    lines = content.splitlines(keepends=True)
    lines[task.line_no] = re.sub(r"- \[[ xX~!]\]", f"- [{mark}]", lines[task.line_no], count=1)
    return "".join(lines)

# ------------------------------------------------------------- planning ----


def plan_shift(tasks: list[Task], budget: int, reserve: int = 1) -> tuple[list[Task], list[Task]]:
    """Pick tasks for one shift.

    A fresh shift has the most headroom, so the biggest high-value work goes
    first; small tasks then fill the gaps (greedy knapsack). `reserve` units are
    kept back for handoff notes and the brief.
    """
    usable = max(0, budget - reserve)
    pending = [t for t in tasks if t.pending]
    # partial tasks resume first, then priority, then biggest first
    order = sorted(pending, key=lambda t: (t.mark != "~", PRIORITIES[t.priority], -t.size, t.line_no))
    chosen, left = [], usable
    for t in order:
        if t.size <= left:
            chosen.append(t)
            left -= t.size
    deferred = [t for t in pending if t not in chosen]
    return chosen, deferred

# ------------------------------------------------------------ executors ----


@dataclass
class Result:
    status: str                       # done | partial | blocked | limit
    summary: str
    decisions: list[str] = field(default_factory=list)
    branch: str | None = None
    commits: int = 0


RESULT_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.S)


def parse_agent_report(text: str) -> Result:
    blocks = RESULT_RE.findall(text)
    for blob in reversed(blocks):
        try:
            data = json.loads(blob)
        except json.JSONDecodeError:
            continue
        status = data.get("status", "partial")
        if status not in ("done", "partial", "blocked"):
            status = "partial"
        return Result(status=status, summary=str(data.get("summary", "")).strip() or text.strip()[-300:],
                      decisions=[str(d) for d in data.get("decisions", [])][:5])
    return Result(status="partial", summary=text.strip()[-300:] or "(no output)")


def build_prompt(task: Task, handoff: str) -> str:
    resume = f"\n\nA previous shift already worked on this. Its handoff note:\n{handoff}\n" if handoff else ""
    return f"""You are the night-shift engineer. The owner is asleep and cannot answer questions.

Task: {task.text}{resume}
Rules:
- Work only inside the current directory (a git worktree on its own branch).
- Do as much as you safely can. If a choice really needs the owner, make the
  most reversible choice, note it, and move on.
- Commit your work with clear messages.
- Finish with exactly one fenced json block:
```json
{{"status": "done|partial|blocked", "summary": "what you did, 1-3 sentences",
  "decisions": ["questions the owner must decide in the morning"]}}
```"""


def git(repo: Path, *args: str, check: bool = True) -> str:
    out = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if check and out.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {out.stderr.strip()}")
    return out.stdout.strip()


class ClaudeExecutor:
    """Runs each task with headless Claude Code in its own git worktree."""

    def __init__(self, repo: Path, claude_bin: str = "claude", timeout: int = 3600, extra_args: list[str] | None = None):
        self.repo, self.claude_bin, self.timeout = repo, claude_bin, timeout
        self.extra_args = extra_args if extra_args is not None else ["--permission-mode", "acceptEdits"]

    def run(self, task: Task, handoff: str) -> Result:
        branch = f"nightshift/{task.slug}"
        wt = state_dir(self.repo) / "worktrees" / task.slug
        if not wt.exists():
            exists = git(self.repo, "branch", "--list", branch)
            if exists:
                git(self.repo, "worktree", "add", str(wt), branch)
            else:
                git(self.repo, "worktree", "add", "-b", branch, str(wt))
        base = git(wt, "rev-parse", "HEAD")
        cmd = [self.claude_bin, "-p", build_prompt(task, handoff), "--output-format", "json", *self.extra_args]
        try:
            proc = subprocess.run(cmd, cwd=wt, capture_output=True, text=True, timeout=self.timeout)
        except subprocess.TimeoutExpired:
            return Result("partial", f"timed out after {self.timeout}s", branch=branch)
        text = proc.stdout
        try:
            payload = json.loads(proc.stdout)
            text = payload.get("result", "") or ""
            is_error = payload.get("is_error", False)
        except json.JSONDecodeError:
            is_error = proc.returncode != 0
        if is_error or proc.returncode != 0:
            msg = (text or proc.stderr or "").strip()
            if LIMIT_PATTERNS.search(msg):
                return Result("limit", msg[-200:], branch=branch)
            return Result("blocked", f"agent error: {msg[-200:]}", branch=branch)
        # commit anything the agent left uncommitted
        if git(wt, "status", "--porcelain"):
            git(wt, "add", "-A")
            git(wt, "commit", "-q", "-m", f"nightshift: wip on '{task.text}'")
        result = parse_agent_report(text)
        result.branch = branch
        result.commits = int(git(wt, "rev-list", "--count", f"{base}..HEAD") or 0)
        return result


class DryExecutor:
    """Pretends to work. Deterministic per task, for trying the flow without spending tokens."""

    def run(self, task: Task, handoff: str) -> Result:
        h = int(hashlib.sha1(task.text.encode()).hexdigest(), 16)
        status = ["done", "done", "done", "partial", "blocked"][h % 5]
        decisions = [f"'{task.text}': 방식 A와 B 중 어느 쪽으로 갈까요?"] if status != "done" else []
        return Result(status, f"(dry run) {status}: {task.text}", decisions, branch=f"nightshift/{task.slug}")

# ---------------------------------------------------------------- state ----


def now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def state_dir(repo: Path) -> Path:
    d = repo / STATE_DIR
    d.mkdir(parents=True, exist_ok=True)
    ignore = d / ".gitignore"
    if not ignore.exists():
        ignore.write_text("*\n")  # keep logs and worktrees out of the owner's git status
    return d


def load_log(repo: Path) -> list[dict]:
    p = repo / STATE_DIR / "log.jsonl"
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def append_log(repo: Path, entry: dict) -> None:
    p = state_dir(repo) / "log.jsonl"
    with p.open("a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def handoff_path(repo: Path, task: Task) -> Path:
    return repo / STATE_DIR / "handoff" / f"{task.slug}.md"


def write_handoff(repo: Path, task: Task, res: Result) -> None:
    p = handoff_path(repo, task)
    p.parent.mkdir(parents=True, exist_ok=True)
    note = [f"## Shift {now()} — {res.status}", "", res.summary, ""]
    if res.decisions:
        note += ["Open questions:"] + [f"- {d}" for d in res.decisions] + [""]
    with p.open("a") as f:
        f.write("\n".join(note) + "\n")

# ------------------------------------------------------------- commands ----


def cmd_plan(repo: Path, queue: Path, budget: int) -> list[Task]:
    tasks = parse_queue(queue.read_text())
    chosen, deferred = plan_shift(tasks, budget)
    print(f"🌙 Shift plan — budget {budget} units (1 reserved for handoff)\n")
    for t in chosen:
        tag = "resume " if t.mark == "~" else ""
        print(f"  ▶ [{t.size:>2}] {tag}{t.text}  ({t.priority}, size by {t.sized_by})")
    if deferred:
        print("\n  next shift:")
        for t in deferred:
            print(f"    · [{t.size:>2}] {t.text}")
    if not chosen and not deferred:
        print("  queue is empty — sleep well 😴")
    return chosen


def cmd_run(repo: Path, queue: Path, budget: int, executor) -> int:
    chosen = cmd_plan(repo, queue, budget)
    shift_id = now()
    append_log(repo, {"event": "shift_start", "at": shift_id, "budget": budget, "tasks": [t.text for t in chosen]})
    used = 0
    for t in chosen:
        print(f"\n⚙️  {t.text}")
        prev = handoff_path(repo, t)
        res = executor.run(t, prev.read_text() if prev.exists() else "")
        if res.status == "limit":
            print("   ⛔ usage limit hit — stopping this shift, task stays queued")
            append_log(repo, {"event": "limit", "at": now(), "shift": shift_id, "task": t.text})
            break
        used += t.size
        write_handoff(repo, t, res)
        mark = {"done": "x", "partial": "~", "blocked": "!"}[res.status]
        queue.write_text(set_mark(queue.read_text(), t, mark))
        append_log(repo, {"event": "task", "at": now(), "shift": shift_id, "task": t.text, **asdict(res)})
        print(f"   → {res.status}: {res.summary}")
    append_log(repo, {"event": "shift_end", "at": now(), "shift": shift_id, "used": used})
    return 0


def cmd_brief(repo: Path, queue: Path, since_hours: float = 16) -> str:
    cutoff = dt.datetime.now() - dt.timedelta(hours=since_hours)
    log = [e for e in load_log(repo) if dt.datetime.fromisoformat(e["at"]) >= cutoff]
    tasks = [e for e in log if e["event"] == "task"]
    latest: dict[str, dict] = {}
    for e in tasks:
        latest[e["task"]] = e
    shifts = sum(1 for e in log if e["event"] == "shift_start")
    limits = sum(1 for e in log if e["event"] == "limit")
    by = {s: [e for e in latest.values() if e["status"] == s] for s in ("done", "partial", "blocked")}
    decisions = [d for e in latest.values() for d in e.get("decisions", [])]
    remaining = [t for t in parse_queue(queue.read_text()) if t.pending]

    out = [f"# ☀️ 모닝 브리핑 — {dt.date.today().isoformat()}", "",
           f"밤새 **{shifts}번 근무**, 작업 **{len(latest)}개** 처리"
           + (f", 한도에 {limits}번 걸림" if limits else "") + ".", ""]
    if decisions:
        out += ["## 🙋 결정이 필요한 것", ""] + [f"{i}. {d}" for i, d in enumerate(decisions[:3], 1)]
        if len(decisions) > 3:
            out.append(f"   …외 {len(decisions) - 3}개는 handoff 노트 참고")
        out.append("")
    labels = {"done": "✅ 끝남", "partial": "🔶 진행 중 (다음 근무에서 이어감)", "blocked": "⛔ 막힘"}
    for s, label in labels.items():
        if by[s]:
            out += [f"## {label}", ""]
            for e in by[s]:
                br = f" — `{e['branch']}`" if e.get("branch") else ""
                commits = f", 커밋 {e['commits']}개" if e.get("commits") else ""
                out.append(f"- **{e['task']}**{br}{commits}  \n  {e['summary']}")
            out.append("")
    if remaining:
        out += ["## 📋 아직 남은 큐", ""] + [f"- {t.text} ({t.size})" for t in remaining] + [""]
    if not latest:
        out += ["어젯밤엔 한 일이 없어요. `queue.md`에 할 일을 적고 `run`을 예약해 두세요.", ""]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["plan", "run", "brief"])
    ap.add_argument("--repo", default=".", help="git repo to work in (default: .)")
    ap.add_argument("--queue", default="queue.md", help="queue file, relative to repo")
    ap.add_argument("--budget", type=int, default=12, help="work units this shift can afford")
    ap.add_argument("--executor", choices=["claude", "dry"], default="claude")
    ap.add_argument("--dry-run", action="store_true", help="same as --executor dry")
    ap.add_argument("--claude-bin", default="claude")
    ap.add_argument("--timeout", type=int, default=3600, help="seconds per task")
    ap.add_argument("--out", help="brief: write to this file instead of stdout")
    ap.add_argument("--since-hours", type=float, default=16)
    a = ap.parse_args(argv)

    repo = Path(a.repo).resolve()
    queue = repo / a.queue
    if not queue.exists():
        print(f"no queue at {queue} — create one (see queue.example.md)", file=sys.stderr)
        return 1
    if a.command == "plan":
        cmd_plan(repo, queue, a.budget)
        return 0
    if a.command == "run":
        ex = DryExecutor() if a.dry_run or a.executor == "dry" else ClaudeExecutor(repo, a.claude_bin, a.timeout)
        return cmd_run(repo, queue, a.budget, ex)
    text = cmd_brief(repo, queue, a.since_hours)
    if a.out:
        Path(a.out).write_text(text)
        print(f"brief written to {a.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
