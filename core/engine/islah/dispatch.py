#!/usr/bin/env python3
"""Dispatch — turn a greenlit card into a real, VERIFIED fix on its own branch.

Rules of engagement (deliberate, and non-negotiable):
  * Only picks up cards you GREENLIT (status: fixing). Never self-selects work.
  * A card with no brief (root cause + fix) is NEVER dispatched.
  * LANES: cards touching the same files go together, one branch, one agent.
    Two agents must never edit the same file.
  * Max 2 lanes at once — one repo, one simulator.
  * Isolated worktree + isolated DerivedData per lane (BUILDING.md: worktrees
    share one build folder and will clobber each other otherwise).
  * A fix MUST pass a check it can fail: the app builds AND the tests pass.
    No green gate -> the card comes back marked failed, not "done".
  * NEVER merges to main. NEVER bumps the build number. NEVER archives.
    It leaves a branch. You always click approve.

Usage:
    python3 dispatch.py --plan     # show the lanes + conflicts. Touches nothing.
    python3 dispatch.py --go       # actually dispatch (max 2 lanes)
"""
import argparse
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from store import Ledger  # noqa: E402

REPOS = {"quran-garden": Path("/Volumes/AOS-X/project/quran-tools")}
CLI = str(Path(__file__).resolve().parent / "cli.py")
QUEUE_STATUS = "fixing"          # what greenlight sets
MAX_LANES = 2                    # one repo, one simulator
SIM = "865E840C-310E-4279-9C3A-906A6C715EE1"   # iPhone 16 Pro, pinned in BUILDING.md

PROMPT = """You are fixing REAL, GREENLIT issues in the Quran Garden iOS app (QuranTools).

FIRST: read `ios/AGENTS.md` and `ios/BUILDING.md`. They are the house rules. Obey them.

You are on branch `{branch}` in an isolated worktree. You own these cards — and ONLY these:

{cards}

Each card already has a diagnosis from triage. Trust it as a starting point, but VERIFY it
against the actual code before you act — triage can be wrong.

WHAT DONE MEANS (all of it, or the card is not done):
1. The fix is implemented, minimally and in the house style.
2. The app BUILDS:
   cd ios && xcodebuild -scheme QuranTools -destination 'id={sim}' \\
       -derivedDataPath /tmp/qt-dd-{lane} -quiet build 2>&1 | grep -E "(error:|BUILD)" | head
3. The tests PASS:
   cd ios && xcodebuild -scheme QuranTools -destination 'id={sim}' \\
       -derivedDataPath /tmp/qt-dd-{lane} -only-testing:QuranToolsTests test 2>&1 \\
       | grep -E "(Test run with|FAILED|SUCCEED)" | tail -3
   If a card's fix intentionally changes behaviour that an existing test locks in,
   REWRITE that test to assert the new contract — and say so explicitly.
4. Commit on THIS branch, one commit per card, message: "fix(<area>): <what> (<card id>)"

HARD RULES:
- NEVER merge, rebase onto, or push to main. Leave the branch. The operator approves.
- NEVER bump CFBundleVersion. NEVER run `xcodebuild archive`. NEVER run qt-preflight.
- NEVER edit the .xcodeproj directly — this project uses XcodeGen (`project.yml` + `xcodegen`).
- Do not touch files outside what these cards need. Another agent may own them.
- If the build or tests will not go green, STOP. Report the failure honestly. A card that
  does not build is NOT done, and you must not claim it is.

WHEN A CARD IS GENUINELY DONE (built + tests green + committed), record it:

python3 {cli} set '<card id>' --status awaiting-approval

Then, for each card, print one line: `<card id> | DONE <sha> | <one sentence of what changed>`
or `<card id> | FAILED | <why>`. Show the build/test output as evidence. Do not assert; prove.
"""


def brief_ok(b):
    return bool(b.root_cause and b.fix_approach)


def files_of(b):
    return {re.split(r"[:#]", r.strip())[0].split("/")[-1] for r in (b.code_refs or []) if r.strip()}


def lanes_for(cards):
    """Union-find: cards sharing any file land in the same lane. No two agents share a file."""
    lanes = []
    for c in cards:
        f = files_of(c)
        hit = [ln for ln in lanes if ln["files"] & f]
        if not hit:
            lanes.append({"cards": [c], "files": set(f)})
        else:
            first = hit[0]
            first["cards"].append(c)
            first["files"] |= f
            for other in hit[1:]:
                first["cards"] += other["cards"]
                first["files"] |= other["files"]
                lanes.remove(other)
    for i, ln in enumerate(lanes, 1):
        ln["name"] = "-".join(c.id.replace("#", "") for c in ln["cards"])[:40] or f"lane{i}"
    return lanes


def run_lane(lane, repo, dry=True):
    name = lane["name"]
    branch = f"islah/{name}"
    wt = Path(f"/tmp/qt-wt-{name}")
    ids = ", ".join(c.id for c in lane["cards"])
    if dry:
        return f"[plan] {branch}: {ids}"
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-f", str(wt), "-b", branch, "main"],
                   capture_output=True, text=True)
    if not wt.exists():
        return f"✗ {name}: worktree failed"
    cards_txt = "\n\n".join(
        f"--- {c.id} ({c.severity or '-'}) {c.title}\n"
        f"REPORTED (verbatim): {c.source_text or c.symptom or ''}\n"
        f"ROOT CAUSE (triage): {c.root_cause}\n"
        f"PROPOSED FIX (triage): {c.fix_approach}\n"
        f"CODE REFS: {', '.join(c.code_refs or [])}"
        for c in lane["cards"])
    prompt = PROMPT.format(branch=branch, cards=cards_txt, sim=SIM, lane=name, cli=CLI)
    r = subprocess.run(
        ["claude", "-p", prompt, "--permission-mode", "bypassPermissions"],
        cwd=str(wt), capture_output=True, text=True, timeout=5400)
    out = (r.stdout or "") + (r.stderr or "")
    done = [ln for ln in out.splitlines() if "| DONE" in ln or "| FAILED" in ln]
    return f"{branch} ({ids})\n" + ("\n".join("    " + d for d in done) if done else f"    no verdict; tail: {out[-200:]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--go", action="store_true", help="actually dispatch (default: plan only)")
    a = ap.parse_args()

    lg = Ledger()
    queued = [b for b in lg.all() if b.status == QUEUE_STATUS and b.app in REPOS]
    ready = [b for b in queued if brief_ok(b)]
    blocked = [b for b in queued if not brief_ok(b)]

    if not queued:
        briefed = [b for b in lg.all() if b.is_open() and brief_ok(b) and b.app in REPOS]
        print("Nothing greenlit yet — swipe right on a card to queue it.\n")
        print(f"PREVIEW — if you greenlit all {len(briefed)} briefed cards, the lanes would be:\n")
        for ln in lanes_for(briefed):
            ids = ", ".join(c.id for c in ln["cards"])
            print(f"  lane {ln['name'][:28]:30} {ids}")
            print(f"       shares files: {', '.join(sorted(ln['files']))[:88] or '(none)'}")
        print(f"\n{len(lanes_for(briefed))} lanes · cap {MAX_LANES} at a time · cards in a lane share files "
              f"and MUST go together.")
        return

    for b in blocked:
        print(f"  ⚠ {b.id} greenlit but has no brief — will not dispatch (run triage first)")
    lanes = lanes_for(ready)
    print(f"{len(ready)} greenlit card(s) → {len(lanes)} lane(s), max {MAX_LANES} at a time\n")
    for ln in lanes:
        print(f"  {ln['name'][:30]:32} {', '.join(c.id for c in ln['cards'])}")
    if not a.go:
        print("\n(plan only — nothing dispatched. re-run with --go)")
        return

    repo = REPOS[ready[0].app]
    print(f"\ndispatching into {repo} …\n")
    with ThreadPoolExecutor(max_workers=MAX_LANES) as ex:
        for res in ex.map(lambda ln: run_lane(ln, repo, dry=False), lanes):
            print(res)


if __name__ == "__main__":
    main()
