#!/usr/bin/env python3
"""Auto-triage — an agent investigates each untriaged issue and writes the brief.

Rules of engagement (deliberate):
  * INVESTIGATION ONLY. The agent may read/grep the app repo. It may not edit app code.
  * It NEVER changes status. Triage enriches; the operator still decides.
  * It fills: root_cause, fix_approach, code_refs, severity.

Usage:
    python3 triage.py --id qg#2        # triage one issue
    python3 triage.py --limit 5        # triage the next 5 untriaged
    python3 triage.py --list           # show what needs triage
"""
import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from store import Ledger  # noqa: E402

REPOS = {
    "quran-garden": Path("/Volumes/AOS-X/project/quran-tools"),
    "deenoverdunya": Path.home() / "project" / "deenoverdunya",
}
CLI = str(Path(__file__).resolve().parent / "cli.py")
TRIAGEABLE = ("new", "triaging", "needs-decision", "confirmed", "reopened")

PROMPT = """You are triaging a real issue for the {app} app. INVESTIGATION ONLY.

Issue id:  {id}
Title:     {title}
Type:      {kind}
Reported:  {symptom}

Your job:
1. Search this repo (Grep / Glob / Read) and work out what actually causes this,
   or — if it is a feature/idea rather than a bug — where it would be implemented.
2. Decide:
   - root cause: LEAD with one plain-language sentence a non-engineer understands
     ("This was never built - a past fix locked the old behaviour in"). Then at most
     2 more sentences of code specifics. Hard cap: 3 sentences total.
   - the fix: LEAD with one plain sentence of what changes for the user. Then the
     concrete implementation, specific enough to build from. Hard cap: 5 sentences.
   - code refs: the actual files and line numbers involved.
   - severity: 1=urgent (broken/crash), 2=high, 3=normal, 4=low/polish.
3. Write your findings back by running EXACTLY this command, with your values:

python3 {cli} set '{id}' --root-cause "..." --fix-approach "..." --code-refs "File.swift:120,Other.swift:44" --severity 3

HARD RULES:
- Do NOT edit, write, or patch any app code. You are diagnosing, not fixing.
- Do NOT change the issue status. The operator decides what happens next.
- Run the `set` command exactly once, then stop.
- If you genuinely cannot find the cause, still run the command with:
  --root-cause "Could not determine from the code - needs a reproduction"
- Write for a smart non-engineer: plain language first, code specifics second.

When the set command has succeeded, print DONE.
"""


def needs_triage(lg):
    out = []
    for b in lg.all():
        if b.root_cause:
            continue
        if b.status not in TRIAGEABLE:
            continue
        if b.app not in REPOS or not REPOS[b.app].exists():
            continue
        out.append(b)
    return out


def triage_one(b, timeout=600):
    repo = REPOS[b.app]
    prompt = PROMPT.format(app=b.app, id=b.id, title=b.title, kind=b.kind or "bug",
                           symptom=b.symptom or "(no description given)", cli=CLI)
    print(f"→ {b.id}  {b.title[:58]}", flush=True)
    try:
        r = subprocess.run(
            ["claude", "-p", prompt,
             "--permission-mode", "bypassPermissions",
             "--allowedTools", "Read,Grep,Glob,Bash",
             "--disallowedTools", "Edit,Write,NotebookEdit"],
            cwd=str(repo), capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        print(f"   timeout after {timeout}s", flush=True)
        return False
    tail = ((r.stdout or "") + (r.stderr or "")).strip().replace("\n", " ")[-140:]
    fresh = Ledger().get(b.id)
    ok = bool(fresh and fresh.root_cause)
    print(f"   {'✓ brief written' if ok else '✗ no brief'}  {tail[:110]}", flush=True)
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", help="triage a single issue")
    ap.add_argument("--limit", type=int, default=3, help="how many to triage this run")
    ap.add_argument("--list", action="store_true", help="show what needs triage")
    a = ap.parse_args()

    lg = Ledger()
    if a.id:
        b = lg.get(a.id)
        if not b:
            sys.exit(f"not found: {a.id}")
        sys.exit(0 if triage_one(b) else 1)

    pending = needs_triage(lg)
    if a.list:
        print(f"{len(pending)} need triage:")
        for b in pending:
            print(f"  {b.id:8} {b.app:14} {b.title[:52]}")
        return
    if not pending:
        print("nothing to triage — every open issue has a brief.")
        return

    batch = pending[: a.limit]
    print(f"triaging {len(batch)} of {len(pending)} pending\n")
    done = sum(triage_one(b) for b in batch)
    print(f"\n{done}/{len(batch)} briefs written · {len(pending) - done} still pending")


if __name__ == "__main__":
    main()
