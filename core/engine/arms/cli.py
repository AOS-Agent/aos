"""`aos arm` — operator-facing verbs over the arm engine.

    aos arm status [--json]     what is on this machine and whether it works
    aos arm list                the manifest, grouped by tier
    aos arm add <id> --apply    install it (converges; safe to re-run)
    aos arm plan <id> [--purge] exactly what removing it would do
    aos arm remove <id> --apply do it

Every mutating verb is a DRY RUN by default. --apply is always required.

Status is the default because that is the question worth asking most often.
"""

from __future__ import annotations

import argparse
import json
import sys

from .install import InstallRefused
from .install import apply as install_apply
from .install import plan as install_plan
from .manifest import ManifestError, load_manifest
from .probe import ABSENT, ACTIVE, BROKEN, DEGRADED, probe_all, unmanaged_labels
from .remove import RemovalRefused, apply, plan

# Monochrome by operator design law: state is carried by the word and the
# reason, never by colour. These marks read correctly in any terminal.
MARK = {ACTIVE: "  ok  ", DEGRADED: " warn ", BROKEN: "BROKEN", ABSENT: "  --  "}


def cmd_status(args: argparse.Namespace) -> int:
    manifest = load_manifest()
    states = probe_all(manifest)

    if args.json:
        print(json.dumps({
            "schema": manifest.schema,
            "source": str(manifest.source),
            "modules": [
                {"id": s.id, "name": s.name, "tier": s.tier, "kind": s.kind,
                 "status": s.status, "why": s.why} for s in states
            ],
            "foreign": [
                {"label": f.label, "name": f.name, "note": f.note}
                for f in manifest.foreign
            ],
            "unmanaged": unmanaged_labels(manifest),
        }, indent=2))
        return 0

    for tier in ("core", "experimental"):
        rows = [s for s in states if s.tier == tier]
        if not rows:
            continue
        print(f"\n{tier.upper()}")
        for s in rows:
            why = f"   {s.why}" if s.why else ""
            print(f"  [{MARK[s.status]}] {s.name:<24}{s.kind:<10}{s.status}{why}")

    if manifest.foreign:
        print("\nUNMANAGED  (observed, not owned by AOS — never touched)")
        for f in manifest.foreign:
            print(f"  [      ] {f.name:<24}{f.label}")

    stray = unmanaged_labels(manifest)
    if stray:
        print("\nNOT IN THE MANIFEST — coverage gap, please adopt or declare foreign:")
        for label in stray:
            print(f"  ? {label}")

    counts: dict[str, int] = {}
    for s in states:
        counts[s.status] = counts.get(s.status, 0) + 1
    print("\n" + ", ".join(f"{v} {k}" for k, v in sorted(counts.items())))
    return 1 if counts.get(BROKEN) else 0


def cmd_list(args: argparse.Namespace) -> int:
    manifest = load_manifest()
    for tier in ("core", "experimental"):
        rows = [m for m in manifest.modules if m.tier == tier]
        print(f"\n{tier.upper()}  ({len(rows)})")
        for m in rows:
            flags = []
            if m.connector:
                flags.append("connector")
            if m.consent:
                flags.append("sensitive")
            if not m.removable:
                flags.append("permanent")
            tail = f"  [{', '.join(flags)}]" if flags else ""
            print(f"  {m.id:<20}{m.kind:<10}{m.tagline}{tail}")
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    try:
        steps = plan(args.module, purge=args.purge)
    except RemovalRefused as exc:
        print(f"refused: {exc}")
        return 2
    print(f"Removing '{args.module}' would:")
    for s in steps:
        print(s)
    if not args.purge:
        print("\n  Data (vaults, databases, models) is not touched. Use --purge to")
        print("  also remove the service venv and instance files.")
    print("\n  Nothing has changed. Re-run with `remove --apply` to execute.")
    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    try:
        steps = plan(args.module, purge=args.purge)
    except RemovalRefused as exc:
        print(f"refused: {exc}")
        return 2

    if not args.apply:
        print(f"Removing '{args.module}' would:")
        for s in steps:
            print(s)
        print("\n  Dry run. Add --apply to execute.")
        return 0

    done, failed = apply(steps)
    for d in done:
        print(f"  ok    {d}")
    for f in failed:
        print(f"  FAIL  {f}")
    return 1 if failed else 0


def cmd_add(args: argparse.Namespace) -> int:
    try:
        p = install_plan(args.module, force=args.force)
    except InstallRefused as exc:
        print(f"refused: {exc}")
        return 2

    print(f"Installing '{args.module}':")
    for s in p.steps:
        print(s)

    if p.already_done:
        print("\n  Nothing to do — already converged.")
        return 0

    if p.blocked:
        print("\n  Blocked. These need you before anything can be installed:")
        for s in p.blocked:
            print(f"    · {s.target} — {s.detail}")
        print("\n  Nothing was changed.")
        return 2

    if not args.apply:
        print(f"\n  Dry run — {len(p.pending)} step(s) would run. Add --apply to execute.")
        return 0

    try:
        done, failed = install_apply(p)
    except InstallRefused as exc:
        print(f"refused: {exc}")
        return 2
    for d in done:
        print(f"  ok    {d}")
    for f in failed:
        print(f"  FAIL  {f}")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="aos arm", description="Manage AOS arms")
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("status", help="what is installed and whether it works")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_status)

    ls = sub.add_parser("list", help="the manifest, grouped by tier")
    ls.set_defaults(func=cmd_list)

    pl = sub.add_parser("plan", help="show exactly what removing a module would do")
    pl.add_argument("module")
    pl.add_argument("--purge", action="store_true", help="also remove service venv/instance files")
    pl.set_defaults(func=cmd_plan)

    ad = sub.add_parser("add", help="install a module (converges toward active)")
    ad.add_argument("module")
    ad.add_argument("--apply", action="store_true", help="actually execute (default: dry run)")
    ad.add_argument("--force", action="store_true",
                    help="rewrite plists from templates even if the module is working")
    ad.set_defaults(func=cmd_add)

    rm = sub.add_parser("remove", help="remove a module")
    rm.add_argument("module")
    rm.add_argument("--apply", action="store_true", help="actually execute (default: dry run)")
    rm.add_argument("--purge", action="store_true")
    rm.set_defaults(func=cmd_remove)

    args = p.parse_args(argv)
    if not getattr(args, "func", None):
        args = p.parse_args((argv or []) + ["status"])
    try:
        return args.func(args)
    except ManifestError as exc:
        print(f"manifest error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
