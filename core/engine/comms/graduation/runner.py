#!/usr/bin/env python3
"""Graduation runner.

Evaluates all people, applies instant demotions, queues promotions
for operator approval. Run daily via cron or manually.

Usage:
    python3 runner.py                    # evaluate + apply
    python3 runner.py --dry-run          # show what would happen
    python3 runner.py confirm <person_id> accept|reject

Promotions are queued in ~/.aos/work/comms/graduation_proposals.json.
Demotions are applied instantly with audit log.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

# People DB
_PEOPLE_SERVICE = Path.home() / ".aos" / "services" / "people"
sys.path.insert(0, str(_PEOPLE_SERVICE))
import db as people_db

log = logging.getLogger(__name__)

PROPOSALS_PATH = Path.home() / ".aos" / "work" / "comms" / "graduation_proposals.json"
AUDIT_LOG = Path.home() / ".aos" / "logs" / "comms-graduation.log"
TRUST_PATH = Path.home() / ".aos" / "config" / "trust.yaml"


def _load_trust_config() -> dict:
    try:
        import yaml
        with open(TRUST_PATH) as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _save_trust_config(config: dict):
    import yaml
    TRUST_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(TRUST_PATH, "w") as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True)


def _write_audit(person_id: str, name: str, from_level: int, to_level: int, reason: str):
    AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry = json.dumps({
        "ts": time.time(),
        "person_id": person_id,
        "name": name,
        "from_level": from_level,
        "to_level": to_level,
        "reason": reason,
    })
    with open(AUDIT_LOG, "a") as f:
        f.write(entry + "\n")


def _load_proposals() -> list[dict]:
    if PROPOSALS_PATH.exists():
        try:
            return json.loads(PROPOSALS_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            return []
    return []


def _save_proposals(proposals: list[dict]):
    PROPOSALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROPOSALS_PATH.write_text(json.dumps(proposals, indent=2))


def _set_person_level(trust_config: dict, person_id: str, level: int):
    """Set a person's comms trust level in the config."""
    comms = trust_config.setdefault("comms", {})
    per_person = comms.setdefault("per_person", {})
    per_person[person_id] = {"level": level, "updated_at": time.time()}


def _notify(message: str):
    """Send Telegram notification."""
    try:
        import subprocess
        script = Path.home() / "aos" / "core" / "lib" / "notify.py"
        if script.exists():
            subprocess.run(
                [sys.executable, str(script), message],
                capture_output=True, timeout=10,
            )
    except Exception:
        pass


# ── Run ──────────────────────────────────────────────────

def run(dry_run: bool = False) -> dict:
    """Evaluate all people and process graduation changes."""
    from evaluator import evaluate_all

    trust_config = _load_trust_config()
    conn = people_db.connect()

    results = evaluate_all(conn, trust_config)
    conn.close()

    demotions = 0
    promotions_queued = 0
    holds = 0
    proposals = _load_proposals()
    existing_pids = {p["person_id"] for p in proposals}

    for r in results:
        if r.can_demote:
            if not dry_run:
                _set_person_level(trust_config, r.person_id, r.recommended_level)
                _write_audit(r.person_id, r.person_name, r.current_level, r.recommended_level, r.reason)
                _notify(f"⚠️ Trust demoted: {r.person_name} → Level {r.recommended_level} ({r.reason})")
            demotions += 1
            log.info(f"  ⬇️  {r.person_name}: Level {r.current_level} → {r.recommended_level} ({r.reason})")

        elif r.can_promote:
            if r.person_id not in existing_pids:
                proposal = {
                    "person_id": r.person_id,
                    "name": r.person_name,
                    "from_level": r.current_level,
                    "to_level": r.recommended_level,
                    "reason": r.reason,
                    "evidence": r.evidence,
                    "proposed_at": time.time(),
                }
                proposals.append(proposal)
                existing_pids.add(r.person_id)
            promotions_queued += 1
            log.info(f"  ⬆️  {r.person_name}: Level {r.current_level} → {r.recommended_level} (queued) — {r.reason}")

        else:
            holds += 1

    if not dry_run:
        _save_trust_config(trust_config)
        _save_proposals(proposals)

    summary = {
        "total_evaluated": len(results),
        "demotions": demotions,
        "promotions_queued": promotions_queued,
        "holds": holds,
        "dry_run": dry_run,
    }

    return summary


# ── Confirm ──────────────────────────────────────────────

def confirm(person_id: str, action: str) -> bool:
    """Accept or reject a graduation proposal.

    Args:
        person_id: Person to confirm
        action: "accept" or "reject"

    Returns:
        True if successful
    """
    proposals = _load_proposals()
    match = None
    remaining = []

    for p in proposals:
        if p["person_id"] == person_id:
            match = p
        else:
            remaining.append(p)

    if not match:
        log.error(f"No pending proposal for {person_id}")
        return False

    if action == "accept":
        trust_config = _load_trust_config()
        _set_person_level(trust_config, person_id, match["to_level"])
        _save_trust_config(trust_config)
        _write_audit(person_id, match["name"], match["from_level"], match["to_level"],
                     f"Approved: {match['reason']}")
        _notify(f"✅ Trust promoted: {match['name']} → Level {match['to_level']}")
        log.info(f"Accepted: {match['name']} → Level {match['to_level']}")

    elif action == "reject":
        _write_audit(person_id, match["name"], match["from_level"], match["from_level"],
                     f"Rejected: {match['reason']}")
        log.info(f"Rejected: {match['name']} stays at Level {match['from_level']}")

    _save_proposals(remaining)
    return True


# ── CLI ──────────────────────────────────────────────────

def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    parser = argparse.ArgumentParser(description="Graduation runner")
    parser.add_argument("--dry-run", action="store_true")
    sub = parser.add_subparsers(dest="command")

    confirm_parser = sub.add_parser("confirm", help="Accept/reject a proposal")
    confirm_parser.add_argument("person_id")
    confirm_parser.add_argument("action", choices=["accept", "reject"])

    args = parser.parse_args()

    if args.command == "confirm":
        success = confirm(args.person_id, args.action)
        sys.exit(0 if success else 1)
    else:
        result = run(dry_run=args.dry_run)
        print()
        print("═" * 50)
        print(f"  Graduation Runner {'(DRY RUN)' if result['dry_run'] else ''}")
        print("═" * 50)
        print(f"  Evaluated:          {result['total_evaluated']}")
        print(f"  Demotions applied:  {result['demotions']}")
        print(f"  Promotions queued:  {result['promotions_queued']}")
        print(f"  Holds:              {result['holds']}")
        print("═" * 50)


if __name__ == "__main__":
    main()
