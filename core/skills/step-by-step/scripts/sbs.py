#!/usr/bin/env python3
"""sbs — step-by-step helper. The parts of the protocol that must be identical
every run: scope/trail rendering, criteria storage, verification, usage log.

  sbs scope    <parent>                         scope block with work IDs
  sbs trail    <parent> [--current <id>]        ✅ A → 🔶 B → ⬜ C
  sbs criteria <subtask> [--size S|M|L] --set "$ cmd :: expect" --set "manual gate" ...
  sbs verify   <subtask|parent>                 run stored checks, print evidence
  sbs log      --task T --parts N --mode M --domain D [--skipped n --splits n --merges n
                                                --initiative slug --phase p --work-id id]

Criteria live in the subtask's work notes (visible in `work show`). A check
line starting with `$` is runnable: the text after `::` must appear in its
output (no `::` → exit 0 is enough). Any other line is a manual gate (👁).
Checks are shell lines the agent wrote and the operator approved at MAP; they
run in the current directory with a 30 s timeout and are printed before running.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

ICON = {"done": "✅", "active": "🔶", "in_progress": "🔶", "cancelled": "⏭",
        "canceled": "⏭", "skipped": "⏭"}
SIZE = {"S": "🟢 S", "M": "🟡 M", "L": "🔴 L"}
LOG = Path(os.environ.get("SBS_LOG") or Path.home() / ".aos" / "logs" / "step-by-step.jsonl")
LOG_KEYS = ("date", "task", "parts", "mode", "domain", "skipped", "splits",
            "merges", "initiative", "phase", "work_id")


def _engine():
    here = Path(__file__).resolve()
    for cand in (here.parents[3] / "engine" / "work",
                 Path.home() / "aos" / "core" / "engine" / "work"):
        if (cand / "backend.py").exists():
            sys.path.insert(0, str(cand))
            import backend  # noqa: E402
            return backend
    sys.exit("sbs: work engine not found")


def _tree(eng, tid):
    tree = eng.get_task_tree(tid)
    if not tree:
        sys.exit(f"sbs: no task {tid}")
    return tree


def _short(title, n=26):
    t = title.split(" — ")[0].split(" -- ")[0]
    if t[:5].lower() == "part " and ":" in t:
        t = t.split(":", 1)[1].strip()
    return t if len(t) <= n else t[: n - 1] + "…"


def _parse_notes(notes):
    """→ (size, [check lines]) from a notes block written by `criteria`."""
    size, checks, in_block = None, [], False
    for line in (notes or "").splitlines():
        s = line.strip()
        if s.startswith("size:"):
            size = s[5:].strip().upper() or None
        elif s == "done-when:":
            in_block = True
        elif in_block and s.startswith("- "):
            checks.append(s[2:])
        elif in_block and s and not s.startswith("- "):
            in_block = False
    return size, checks


def cmd_scope(eng, args):
    tree = _tree(eng, args[0])
    print(f"\n  Scope: {tree['title']}    [{tree['id']}]\n")
    for i, s in enumerate(tree.get("subtasks", []), 1):
        size, _ = _parse_notes(s.get("notes"))
        tag = f" ({size})" if size else ""
        print(f"  {ICON.get(s.get('status'), '⬜')} {i}. {_short(s['title'], 60)}{tag}"
              f"    [{s['id']}]")
    print()


def cmd_trail(eng, args):
    current = args[args.index("--current") + 1] if "--current" in args else None
    tree = _tree(eng, args[0])
    parts = []
    for s in tree.get("subtasks", []):
        status = s.get("status")
        if current:  # explicit cursor: only that part is current
            status = "active" if s["id"] == current else ("todo" if status in ("active", "in_progress") else status)
        icon = ICON.get(status, "⬜")
        parts.append(f"{icon} {_short(s['title'])}")
    print("  " + "  →  ".join(parts))


def cmd_criteria(eng, args):
    tid, size, checks = args[0], None, []
    i = 1
    while i < len(args):
        if args[i] == "--size":
            size, i = args[i + 1].upper(), i + 2
        elif args[i] == "--set":
            checks.append(args[i + 1]); i += 2
        else:
            sys.exit(f"sbs: unknown arg {args[i]}")
    task = eng.get_task(tid) or sys.exit(f"sbs: no task {tid}")
    old_size, old_checks = _parse_notes(task.get("notes"))
    size = size or old_size
    checks = checks or old_checks
    lines = ([f"size: {size}"] if size else []) + ["done-when:"] + [f"- {c}" for c in checks]
    eng.update_task(tid, notes="\n".join(lines))
    print(f"  {tid}  {SIZE.get(size, '')}  {len(checks)} criteria stored")
    for c in checks:
        print(f"    {'$' if c.startswith('$') else '👁'} {c.lstrip('$ ')}")


def _run(check):
    cmd, _, expect = check[1:].partition("::")
    cmd, expect = cmd.strip(), expect.strip()
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
        out = (p.stdout + p.stderr).strip()
    except subprocess.TimeoutExpired:
        return False, cmd, "timeout after 30 s"
    ok = (expect in out) if expect else (p.returncode == 0)
    first = out.splitlines()[0] if out else f"exit {p.returncode}"
    return ok, cmd, (expect if ok and expect else first[:90])


def _verify_one(task):
    size, checks = _parse_notes(task.get("notes"))
    print(f"\n  {task['id']}  {_short(task['title'], 60)}")
    if not checks:
        print("  > ⚠️  no criteria stored")
        return False
    failed = 0
    for c in checks:
        if c.startswith("$"):
            ok, cmd, detail = _run(c)
            failed += not ok
            print(f"  > {'✅' if ok else '❌'} `{cmd}` → {detail}")
        else:
            print(f"  > 👁 {c}")
    return failed == 0


def cmd_verify(eng, args):
    tree = _tree(eng, args[0])
    subs = [s for s in tree.get("subtasks", []) if s.get("status") in ("done", "active", "in_progress")]
    targets = subs if subs else [tree]
    results = [_verify_one(t) for t in targets]
    print()
    sys.exit(0 if all(results) else 1)


def cmd_log(eng, args):
    row = {"date": date.today().isoformat(), "skipped": 0, "splits": 0, "merges": 0}
    i = 0
    while i < len(args):
        key = args[i].lstrip("-").replace("-", "_")
        if key not in LOG_KEYS:
            sys.exit(f"sbs: unknown log field --{key}; allowed: {', '.join(LOG_KEYS)}")
        val = args[i + 1]
        row[key] = int(val) if key in ("parts", "skipped", "splits", "merges") else val
        i += 2
    missing = [k for k in ("task", "parts", "mode", "domain") if k not in row]
    if missing:
        sys.exit(f"sbs: log needs --{' --'.join(missing)}")
    ordered = {k: row[k] for k in LOG_KEYS if k in row}
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f:
        f.write(json.dumps(ordered) + "\n")
    print(f"  logged → {LOG}")


def main():
    if len(sys.argv) < 3 and not (len(sys.argv) == 2 and sys.argv[1] == "log"):
        sys.exit(__doc__)
    cmd, args = sys.argv[1], sys.argv[2:]
    fn = {"scope": cmd_scope, "trail": cmd_trail, "criteria": cmd_criteria,
          "verify": cmd_verify, "log": cmd_log}.get(cmd)
    if not fn:
        sys.exit(__doc__)
    fn(_engine(), args)


if __name__ == "__main__":
    main()
