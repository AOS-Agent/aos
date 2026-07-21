"""Enrichment config — loads config/enrichment.yaml over code defaults.

Missing file or missing keys degrade to defaults (component-lifecycle: no
config = graceful, never a crash). Paths resolve at call time so tests can
point AOS_CONFIG_DIR at a fixture.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml
except Exception:  # pragma: no cover - yaml is a hard dep in AOS, defensive only
    yaml = None


def _config_path() -> Path:
    override = os.environ.get("AOS_CONFIG_DIR")
    if override:
        return Path(override) / "enrichment.yaml"
    # Instance layer wins (per-machine operator choices like propose_commitments
    # — component-lifecycle rule: framework ships defaults, instance opts in).
    instance = Path.home() / ".aos" / "config" / "enrichment.yaml"
    if instance.exists():
        return instance
    # Framework config ships in the repo; runtime reads the same relative path.
    return _repo_root() / "config" / "enrichment.yaml"


def _repo_root() -> Path:
    # …/core/engine/comms/enrich/config.py → repo root is parents[4].
    return Path(__file__).resolve().parents[4]


@dataclass
class EnrichConfig:
    extractor_version: str = "extract@1"
    model: str = "haiku"
    concurrency: int = 3
    call_timeout_s: int = 180

    store_min: float = 0.60
    surface_min: float = 0.80

    min_batch_msgs: int = 15
    max_batch_msgs: int = 40
    max_msg_chars: int = 600

    max_comms_db_bytes: int = 1610612736      # 1.5 GiB
    min_disk_free_bytes: int = 10737418240    # 10 GiB
    bytes_per_entity_estimate: int = 400

    superseded_ttl_days: int = 30

    backup_dir: str = "/Volumes/AOS-X/backups/comms"
    backup_keep: int = 7

    nightly_max_runtime_min: int = 45
    nightly_newest_first: bool = True

    backfill_default_max_hours: float = 8.0
    backfill_newest_first: bool = True

    # Concurrency is a HARD CAP regardless of config (sample §8).
    CONCURRENCY_HARD_CAP: int = field(default=3, init=False, repr=False)

    def __post_init__(self) -> None:
        self.concurrency = max(1, min(int(self.concurrency), self.CONCURRENCY_HARD_CAP))

    @classmethod
    def load(cls, path: Path | None = None) -> "EnrichConfig":
        p = path or _config_path()
        raw: dict[str, Any] = {}
        if yaml is not None and p.exists():
            try:
                raw = yaml.safe_load(p.read_text()) or {}
            except Exception:
                raw = {}
        storage = raw.get("storage", {}) or {}
        gc = raw.get("gc", {}) or {}
        backup = raw.get("backup", {}) or {}
        nightly = raw.get("nightly", {}) or {}
        backfill = raw.get("backfill", {}) or {}
        return cls(
            extractor_version=raw.get("extractor_version", cls.extractor_version),
            model=raw.get("model", cls.model),
            concurrency=raw.get("concurrency", cls.concurrency),
            call_timeout_s=raw.get("call_timeout_s", cls.call_timeout_s),
            store_min=float(raw.get("store_min", cls.store_min)),
            surface_min=float(raw.get("surface_min", cls.surface_min)),
            min_batch_msgs=int(raw.get("min_batch_msgs", cls.min_batch_msgs)),
            max_batch_msgs=int(raw.get("max_batch_msgs", cls.max_batch_msgs)),
            max_msg_chars=int(raw.get("max_msg_chars", cls.max_msg_chars)),
            max_comms_db_bytes=int(storage.get("max_comms_db_bytes", cls.max_comms_db_bytes)),
            min_disk_free_bytes=int(storage.get("min_disk_free_bytes", cls.min_disk_free_bytes)),
            bytes_per_entity_estimate=int(storage.get("bytes_per_entity_estimate", cls.bytes_per_entity_estimate)),
            superseded_ttl_days=int(gc.get("superseded_ttl_days", cls.superseded_ttl_days)),
            backup_dir=backup.get("dir", cls.backup_dir),
            backup_keep=int(backup.get("keep", cls.backup_keep)),
            nightly_max_runtime_min=int(nightly.get("max_runtime_min", cls.nightly_max_runtime_min)),
            nightly_newest_first=bool(nightly.get("newest_first", cls.nightly_newest_first)),
            backfill_default_max_hours=float(backfill.get("default_max_hours", cls.backfill_default_max_hours)),
            backfill_newest_first=bool(backfill.get("newest_first", cls.backfill_newest_first)),
        )
