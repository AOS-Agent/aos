#!/usr/bin/env python3
"""UserPromptSubmit hook — inject a person's mini-profile when the prompt names them.

Phase 5, mention-triggered context. When the operator's prompt names a known
person, this hook adds what the agent already knows about that person: last
interaction, open commitments in both directions, their unanswered questions,
and recent topics — so the agent doesn't ask the operator to re-explain.

HARD BUDGET: <100ms, NO DB queries, NO model calls. All the expensive work
(resolving people, querying comms.db) is done nightly by
core/engine/comms/ambient/profile.py and frozen to JSON files. This hook only:
reads names.json (one small file), scans the prompt for capitalized name tokens,
and reads at most a couple of snapshot files. Privacy is already enforced at
build time — restricted contacts have no snapshot, so they can never appear here.

Hook contract: read hook input JSON on stdin, print a JSON object with
hookSpecificOutput.additionalContext, exit 0. MUST NEVER fail — any error prints
`{}` and exits 0 so a prompt is never blocked.
"""

import json
import re
import sys
from pathlib import Path

CACHE_DIR = Path.home() / ".aos" / "cache" / "ambient"
_MAX_PEOPLE = 2  # cap injected profiles so a name-heavy prompt stays lean
# Common capitalized sentence-openers / pronouns that are never contact lookups.
_STOP = {"i", "i'm", "the", "a", "an", "can", "could", "would", "should", "what",
         "when", "where", "why", "how", "who", "is", "are", "do", "does", "did",
         "please", "let", "let's", "hey", "hi", "ok", "okay", "yes", "no", "and",
         "but", "if", "so", "this", "that", "these", "those", "my", "your", "he",
         "she", "they", "we", "you", "it", "there", "here", "then", "now"}


def _safe_exit(context: str = ""):
    if context:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit", "additionalContext": context}}))
    else:
        print(json.dumps({}))
    sys.exit(0)


def _load_json(path: Path):
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _candidate_keys(prompt: str) -> list[str]:
    """Capitalized-token runs and their tokens, longest first. Cheap regex."""
    keys: list[str] = []
    for run in re.findall(r"\b([A-Z][a-zA-Z'’]+(?:\s+[A-Z][a-zA-Z'’]+)*)", prompt or ""):
        run = run.strip()
        low = run.lower()
        if low not in _STOP:
            keys.append(low)                       # full run, e.g. "faisal khan"
        for tok in run.split():
            t = tok.lower()
            if len(t) >= 3 and t not in _STOP:
                keys.append(t)                     # each token, e.g. "faisal"
    # Preserve order, dedup, longest (multi-word) first for best-match priority.
    seen, ordered = set(), []
    for k in sorted(keys, key=lambda s: (-len(s.split()), -len(s))):
        if k not in seen:
            seen.add(k)
            ordered.append(k)
    return ordered


def _rel_date(ts) -> str:
    if not ts:
        return "unknown"
    return str(ts)[:10]


def _render(snap: dict) -> str:
    name = snap.get("name") or snap.get("person_id")
    parts = [f"**{name}**"]
    li = snap.get("last_interaction")
    if li:
        parts.append(f"last talked {_rel_date(li)}")
    lines = ["- " + ", ".join(parts)]
    ob = snap.get("owed_by_you") or []
    if ob:
        lines.append("  You owe them: " + "; ".join(
            (c.get("what") or "")[:50] + (f" (due {c['due']})" if c.get("due") else "")
            for c in ob[:3]))
    ot = snap.get("owed_to_you") or []
    if ot:
        lines.append("  They owe you: " + "; ".join(
            (c.get("what") or "")[:50] for c in ot[:3]))
    q = snap.get("unanswered_questions") or []
    if q:
        lines.append("  Unanswered from them: " + "; ".join(
            f'"{(x.get("q") or "")[:45]}"' for x in q[:2]))
    tp = snap.get("recent_topics") or []
    if tp:
        lines.append("  Recent topics: " + ", ".join(t[:30] for t in tp[:4]))
    return "\n".join(lines)


def resolve_and_render(prompt: str, cache_dir: Path = CACHE_DIR) -> str:
    """Pure, file-only mention resolution: prompt -> injected context string.

    No DB, no model — reads names.json + at most _MAX_PEOPLE snapshot files.
    Returns "" when nothing matches. This is the whole <100ms budget.
    """
    if not prompt:
        return ""
    index = _load_json(cache_dir / "names.json")
    if not index:
        return ""
    matched: list[str] = []
    for key in _candidate_keys(prompt):
        pid = index.get(key)
        if pid and pid not in matched:
            matched.append(pid)
            if len(matched) >= _MAX_PEOPLE:
                break
    if not matched:
        return ""
    blocks = []
    for pid in matched:
        snap = _load_json(cache_dir / "persons" / f"{pid}.json")
        if snap:
            blocks.append(_render(snap))
    if not blocks:
        return ""
    return "[Ambient — people you mentioned]\n" + "\n".join(blocks)


def main():
    try:
        hook_input = json.loads(sys.stdin.read())
    except Exception:
        hook_input = {}
    prompt = hook_input.get("prompt") or hook_input.get("user_prompt") or ""
    _safe_exit(resolve_and_render(prompt))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        _safe_exit()
