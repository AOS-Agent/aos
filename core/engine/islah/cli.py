#!/usr/bin/env python3
"""
Iṣlāḥ CLI — operate the bug ledger.

    islah add "Untranslated words vanish" --app quran-garden --source apple-notes
    islah list --app quran-garden --open
    islah show qg#1
    islah set qg#1 --status confirmed --confirmed \
        --root-cause "guard drops null gloss" --code-refs WordTranslationStore.swift:157
    islah attempt qg#1 "use AlarmKit full clip" --sha d4e5f6 --gate pass
    islah proof qg#1 --kind after --ref ~/.aos/islah/media/qg1-after.png
    islah approve qg#1
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from store import ALL_STATES, Ledger  # noqa: E402

STATUS_ICON = {
    "new": "•", "triaging": "◦", "needs-info": "?", "confirmed": "✓",
    "needs-decision": "⚠", "fixing": "⚙", "verifying": "🔍",
    "awaiting-approval": "⏳", "approved": "✅", "shipped": "🚀",
    "reopened": "↻", "duplicate": "⧉", "wont-fix": "✕",
}


def _csv(v):
    return [x.strip() for x in v.split(",") if x.strip()] if v else []


def cmd_add(a):
    lg = Ledger()
    kwargs = {k: v for k, v in {
        "app": a.app, "kind": a.kind, "source": a.source, "source_ref": a.source_ref,
        "reporter": a.reporter, "screen": a.screen, "build": a.build,
        "symptom": a.symptom, "classification": a.classification,
        "reproducible": a.reproducible, "severity": a.severity,
    }.items() if v is not None}
    if a.code_refs:
        kwargs["code_refs"] = _csv(a.code_refs)
    if a.tags:
        kwargs["tags"] = _csv(a.tags)
    bug = lg.add(a.title, **kwargs)
    print(f"{STATUS_ICON.get(bug.status,'•')} {bug.id}  {bug.title}  [{bug.status}]")


def cmd_list(a):
    lg = Ledger()
    bugs = lg.list(app=a.app, status=a.status, kind=a.kind, open_only=a.open)
    if not bugs:
        print("(no items)")
        return
    order = {s: i for i, s in enumerate(
        ["needs-decision", "confirmed", "reopened", "fixing", "verifying",
         "awaiting-approval", "triaging", "needs-info", "new",
         "approved", "shipped", "duplicate", "wont-fix"])}
    bugs.sort(key=lambda b: (order.get(b.status, 99), b.id))
    for b in bugs:
        icon = STATUS_ICON.get(b.status, "•")
        app = f" ({b.app})" if b.app else ""
        flag = "  ⚠CONFLICT" if b.conflict else ""
        print(f"{icon} {b.id:<8}{app:<16} {b.kind:<7} {b.status:<17} {b.title}{flag}")


def cmd_show(a):
    lg = Ledger()
    b = lg.get(a.id)
    if not b:
        print(f"not found: {a.id}", file=sys.stderr)
        sys.exit(1)
    print(f"{STATUS_ICON.get(b.status,'•')} {b.id}  [{b.status}]  {b.kind}")
    print(f"  {b.title}")
    for label, val in [
        ("app", b.app), ("source", f"{b.source or ''} {b.source_ref or ''}".strip()),
        ("reporter", b.reporter), ("screen", b.screen),
        ("version/build", f"{b.app_version or ''} {b.build or ''}".strip()),
        ("symptom", b.symptom), ("reproducible", b.reproducible),
        ("classification", b.classification), ("severity", b.severity or None),
        ("root_cause", b.root_cause), ("fix_approach", b.fix_approach),
        ("conflict", b.conflict), ("task", b.task), ("approval", b.approval),
    ]:
        if val:
            print(f"    {label:<15}: {val}")
    if b.code_refs:
        print(f"    code_refs      : {', '.join(b.code_refs)}")
    if b.repro_steps:
        print("    repro_steps    :")
        for s in b.repro_steps:
            print(f"      - {s}")
    if b.attempts:
        print("    attempts       :")
        for at in b.attempts:
            print(f"      #{at['n']} [{at.get('gate_result','?')}] {at['hypothesis']} "
                  f"{('('+at['sha']+')') if at.get('sha') else ''}")
    if b.proof:
        print("    proof          :")
        for p in b.proof:
            print(f"      {p['kind']}: {p['ref']}")


def cmd_set(a):
    lg = Ledger()
    if not lg.get(a.id):
        print(f"not found: {a.id}", file=sys.stderr)
        sys.exit(1)
    fields = {}
    for k in ("status", "kind", "app", "screen", "symptom", "classification",
              "reproducible", "root_cause", "fix_approach", "conflict", "lane",
              "task", "reporter", "build", "app_version"):
        v = getattr(a, k, None)
        if v is not None:
            fields[k] = v
    if a.confirmed:
        fields["confirmed"] = True
    if a.severity is not None:
        fields["severity"] = a.severity
    if a.code_refs is not None:
        fields["code_refs"] = _csv(a.code_refs)
    if a.status and a.status not in ALL_STATES:
        print(f"unknown status: {a.status}", file=sys.stderr)
        sys.exit(1)
    b = lg.update(a.id, **fields)
    print(f"{STATUS_ICON.get(b.status,'•')} {b.id}  [{b.status}]  updated")


def cmd_attempt(a):
    lg = Ledger()
    b = lg.append_attempt(a.id, a.hypothesis, sha=a.sha or "", gate_result=a.gate or "")
    print(f"↻ {b.id}  attempt #{len(b.attempts)} logged [{a.gate or 'pending'}]")


def cmd_proof(a):
    lg = Ledger()
    b = lg.add_proof(a.id, a.kind, a.ref)
    print(f"📎 {b.id}  proof '{a.kind}' added")


def cmd_approve(a):
    lg = Ledger()
    if not lg.get(a.id):
        print(f"not found: {a.id}", file=sys.stderr)
        sys.exit(1)
    b = lg.approve(a.id, by=a.by)
    print(f"✅ {b.id}  approved by {b.approved_by}")


def main():
    p = argparse.ArgumentParser(prog="islah", description="Iṣlāḥ bug ledger")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("add"); s.add_argument("title")
    for opt in ("app", "kind", "source", "source-ref", "reporter", "screen", "build",
                "symptom", "classification", "reproducible", "code-refs", "tags"):
        s.add_argument(f"--{opt}", dest=opt.replace("-", "_"))
    s.add_argument("--severity", type=int)
    s.set_defaults(func=cmd_add)

    s = sub.add_parser("list")
    s.add_argument("--app"); s.add_argument("--status"); s.add_argument("--kind")
    s.add_argument("--open", action="store_true")
    s.set_defaults(func=cmd_list)

    s = sub.add_parser("show"); s.add_argument("id"); s.set_defaults(func=cmd_show)

    s = sub.add_parser("set"); s.add_argument("id")
    for opt in ("status", "kind", "app", "screen", "symptom", "classification",
                "reproducible", "root-cause", "fix-approach", "conflict", "lane",
                "task", "reporter", "build", "app-version", "code-refs"):
        s.add_argument(f"--{opt}", dest=opt.replace("-", "_"))
    s.add_argument("--confirmed", action="store_true")
    s.add_argument("--severity", type=int)
    s.set_defaults(func=cmd_set)

    s = sub.add_parser("attempt"); s.add_argument("id"); s.add_argument("hypothesis")
    s.add_argument("--sha"); s.add_argument("--gate", choices=["pass", "fail"])
    s.set_defaults(func=cmd_attempt)

    s = sub.add_parser("proof"); s.add_argument("id")
    s.add_argument("--kind", required=True, choices=["before", "after"])
    s.add_argument("--ref", required=True)
    s.set_defaults(func=cmd_proof)

    s = sub.add_parser("approve"); s.add_argument("id")
    s.add_argument("--by", default="operator")
    s.set_defaults(func=cmd_approve)

    a = p.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
