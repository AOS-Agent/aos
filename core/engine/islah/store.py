"""
Iṣlāḥ ledger store — the durable per-bug spine.

Bugs live at ~/.aos/islah/bugs.yaml (internal SSD — always writable, unlike AOS-X).
Progressive YAML, mirroring the work system: only id/title/status are required.
A reopen APPENDS to attempts[]; it never rewrites history. That is the whole point —
context is never lost when a fix doesn't stick.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

DATA_DIR = Path(os.path.expanduser("~/.aos/islah"))
BUGS_FILE = DATA_DIR / "bugs.yaml"
MEDIA_DIR = DATA_DIR / "media"

OPEN_STATES = {
    "new", "triaging", "needs-info", "confirmed", "needs-decision",
    "fixing", "verifying", "awaiting-approval", "reopened",
}
CLOSED_STATES = {"approved", "shipped", "duplicate", "wont-fix"}
ALL_STATES = OPEN_STATES | CLOSED_STATES


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _app_prefix(app: Optional[str]) -> str:
    """Per-app scoped id prefix. Registry override lives in config; sane fallback here."""
    known = {"quran-garden": "qg", "deenoverdunya": "dod", "quran-tools": "qg"}
    if app in known:
        return known[app]
    parts = [p for p in (app or "").replace("_", "-").split("-") if p]
    if len(parts) >= 2:
        return "".join(p[0] for p in parts)[:4].lower()
    return ((app or "")[:3] or "t").lower()


@dataclass
class Bug:
    id: str
    title: str
    status: str = "new"
    kind: str = "bug"
    app: Optional[str] = None
    source: Optional[str] = None
    source_ref: Optional[str] = None
    source_text: Optional[str] = None  # the reporter's VERBATIM words
    reporter: Optional[str] = None
    reported: Optional[str] = None
    app_version: Optional[str] = None
    build: Optional[str] = None
    device: Optional[str] = None
    os: Optional[str] = None
    screen: Optional[str] = None
    symptom: Optional[str] = None
    expected: Optional[str] = None
    actual: Optional[str] = None
    repro_steps: list = field(default_factory=list)
    reproducible: Optional[str] = None
    severity: int = 0
    classification: Optional[str] = None
    dedup_group: Optional[str] = None
    lane: Optional[str] = None
    confirmed: bool = False
    root_cause: Optional[str] = None
    code_refs: list = field(default_factory=list)
    fix_approach: Optional[str] = None
    conflict: Optional[str] = None
    task: Optional[str] = None
    repo: Optional[str] = None
    branch: Optional[str] = None
    commits: list = field(default_factory=list)
    fixed_in_build: Optional[str] = None
    build_status: Optional[str] = None
    shipped_in: Optional[str] = None
    attempts: list = field(default_factory=list)
    proof: list = field(default_factory=list)
    approval: Optional[str] = None
    approved_by: Optional[str] = None
    approved_at: Optional[str] = None
    attachments: list = field(default_factory=list)
    tags: list = field(default_factory=list)
    notes: Optional[str] = None
    created: Optional[str] = None
    updated: Optional[str] = None

    def is_open(self) -> bool:
        return self.status in OPEN_STATES


class Ledger:
    def __init__(self, path: Path = BUGS_FILE):
        self.path = path
        self._bugs: dict[str, Bug] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        raw = yaml.safe_load(self.path.read_text()) or {}
        for rec in raw.get("bugs", []):
            bug = Bug(**{k: v for k, v in rec.items() if k in Bug.__annotations__})
            self._bugs[bug.id] = bug

    def _save(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        MEDIA_DIR.mkdir(parents=True, exist_ok=True)
        out = []
        for bug in self._bugs.values():
            d = {k: v for k, v in asdict(bug).items()
                 if v not in (None, [], "", 0) or k in ("id", "title", "status")}
            out.append(d)
        payload = {"version": "0.1", "bugs": out}
        tmp = self.path.with_suffix(".yaml.tmp")
        tmp.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True))
        tmp.replace(self.path)

    def _next_id(self, app: Optional[str]) -> str:
        prefix = _app_prefix(app) if app else "t"
        n = 0
        for bid in self._bugs:
            if bid.startswith(prefix + "#"):
                try:
                    n = max(n, int(bid.split("#", 1)[1]))
                except ValueError:
                    pass
        return f"{prefix}#{n + 1}"

    def add(self, title: str, **kwargs) -> Bug:
        app = kwargs.get("app")
        bug = Bug(id=self._next_id(app), title=title, created=_now(), updated=_now())
        for k, v in kwargs.items():
            if k in Bug.__annotations__ and v is not None:
                setattr(bug, k, v)
        if not bug.reported:
            bug.reported = bug.created
        self._bugs[bug.id] = bug
        self._save()
        return bug

    def get(self, bug_id: str) -> Optional[Bug]:
        return self._bugs.get(bug_id)

    def all(self) -> list[Bug]:
        return list(self._bugs.values())

    def list(self, *, app=None, status=None, kind=None, open_only=False) -> list[Bug]:
        out = self.all()
        if app:
            out = [b for b in out if b.app == app]
        if status:
            out = [b for b in out if b.status == status]
        if kind:
            out = [b for b in out if b.kind == kind]
        if open_only:
            out = [b for b in out if b.is_open()]
        return out

    def update(self, bug_id: str, **fields) -> Bug:
        bug = self._bugs[bug_id]
        for k, v in fields.items():
            if k in Bug.__annotations__:
                setattr(bug, k, v)
        bug.updated = _now()
        self._save()
        return bug

    def set_status(self, bug_id: str, status: str) -> Bug:
        if status not in ALL_STATES:
            raise ValueError(f"unknown status: {status}")
        return self.update(bug_id, status=status)

    def append_attempt(self, bug_id: str, hypothesis: str,
                       sha: str = "", gate_result: str = "") -> Bug:
        """Append-only. Reopening a bug adds attempt N+1; nothing is overwritten."""
        bug = self._bugs[bug_id]
        n = len(bug.attempts) + 1
        bug.attempts.append({
            "n": n, "hypothesis": hypothesis, "sha": sha,
            "gate_result": gate_result, "at": _now(),
        })
        bug.updated = _now()
        self._save()
        return bug

    def add_proof(self, bug_id: str, kind: str, path_or_url: str) -> Bug:
        bug = self._bugs[bug_id]
        bug.proof.append({"kind": kind, "ref": path_or_url, "at": _now()})
        bug.updated = _now()
        self._save()
        return bug

    def approve(self, bug_id: str, by: str = "operator") -> Bug:
        bug = self._bugs[bug_id]
        bug.approval = "approved"
        bug.approved_by = by
        bug.approved_at = _now()
        bug.status = "approved"
        bug.updated = _now()
        self._save()
        return bug
