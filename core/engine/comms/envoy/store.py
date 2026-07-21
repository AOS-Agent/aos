"""Envoy conversation store — one directory per conversation under
~/.aos/work/envoy/<id>/ with mission.yaml, state.json, transcript.jsonl.

Filesystem-as-database: inspectable, greppable, survives everything.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path

import yaml

ROOT = Path.home() / ".aos" / "work" / "envoy"

PHASES = ("kickoff", "active", "escalated", "complete", "stopped", "expired", "capped")
ACTIVE_PHASES = ("kickoff", "active", "escalated")


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:32] or "conv"


class Conversation:
    def __init__(self, path: Path):
        self.path = path
        self.id = path.name

    # ── Creation ──────────────────────────────────────────────────

    @classmethod
    def create(cls, contact: str, name: str, mission: str, success: str,
               constraints: str = "", channel: str = "imessage",
               max_messages: int = 12, expires_days: int = 5,
               dry_run: bool = False) -> "Conversation":
        conv_id = f"{slugify(name)}-{int(time.time()) % 100000}"
        path = ROOT / conv_id
        path.mkdir(parents=True, exist_ok=False)
        c = cls(path)
        c._write_yaml("mission.yaml", {
            "contact": contact,
            "name": name,
            "channel": channel,
            "mission": mission,
            "success": success,
            "constraints": constraints,
            "max_messages": max_messages,
            "expires_days": expires_days,
            "dry_run": dry_run,
            "created": now_iso(),
        })
        c.save_state({
            "phase": "kickoff",
            "last_seen_ts": now_iso(),
            "sent_count": 0,
            "turns": 0,
            "errors": 0,
            "updated": now_iso(),
        })
        return c

    # ── IO ────────────────────────────────────────────────────────

    def _write_yaml(self, fname: str, data: dict) -> None:
        (self.path / fname).write_text(yaml.safe_dump(data, sort_keys=False))

    @property
    def mission(self) -> dict:
        return yaml.safe_load((self.path / "mission.yaml").read_text())

    @property
    def state(self) -> dict:
        return json.loads((self.path / "state.json").read_text())

    def save_state(self, state: dict) -> None:
        state["updated"] = now_iso()
        (self.path / "state.json").write_text(json.dumps(state, indent=1))

    def log_message(self, role: str, text: str, meta: dict | None = None) -> None:
        """role: agent | contact | system"""
        entry = {"ts": now_iso(), "role": role, "text": text}
        if meta:
            entry["meta"] = meta
        with (self.path / "transcript.jsonl").open("a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def transcript(self, limit: int = 40) -> list[dict]:
        f = self.path / "transcript.jsonl"
        if not f.exists():
            return []
        lines = f.read_text().strip().splitlines()
        return [json.loads(x) for x in lines[-limit:]]

    # ── Queries ───────────────────────────────────────────────────

    def is_expired(self) -> bool:
        created = datetime.fromisoformat(self.mission["created"])
        age_days = (datetime.now() - created).total_seconds() / 86400
        return age_days > self.mission.get("expires_days", 5)


def all_conversations() -> list[Conversation]:
    if not ROOT.exists():
        return []
    return [Conversation(p) for p in sorted(ROOT.iterdir())
            if (p / "mission.yaml").exists()]


def active_conversations() -> list[Conversation]:
    return [c for c in all_conversations() if c.state["phase"] in ACTIVE_PHASES]


def get(conv_id: str) -> Conversation | None:
    p = ROOT / conv_id
    if (p / "mission.yaml").exists():
        return Conversation(p)
    # prefix match for convenience
    for c in all_conversations():
        if c.id.startswith(conv_id):
            return c
    return None
