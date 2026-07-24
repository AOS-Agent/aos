"""Tracking/detection config — loads tracking.yaml over code defaults.

Mirrors the enrich config pattern (core/comms/enrich/config.py): a missing
file or missing keys degrade to defaults — no config is never a crash. Paths
resolve at call time so tests can point AOS_CONFIG_DIR at a fixture.

Layer gating (initiative §2): probe and LLM layers are fully implemented but
LOG-ONLY by default — ``probe_enabled`` / ``llm_enabled`` flip them from
"record what we WOULD have done" to actually doing it. They ship off until
the eval set validates thresholds.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

try:
    import yaml
except Exception:  # pragma: no cover - yaml is a hard dep in AOS, defensive only
    yaml = None


def _config_path() -> Path:
    override = os.environ.get("AOS_CONFIG_DIR")
    if override:
        return Path(override) / "tracking.yaml"
    # Instance layer wins (per-machine operator choices), then repo default.
    instance = Path.home() / ".aos" / "config" / "tracking.yaml"
    if instance.exists():
        return instance
    return _repo_root() / "config" / "tracking.yaml"


def _repo_root() -> Path:
    # …/core/qareen/tracking/config.py → repo root is parents[3].
    return Path(__file__).resolve().parents[3]


@dataclass
class TrackingConfig:
    """Detection-pipeline configuration with safe, inert-by-default values."""

    # Confidence → action thresholds (initiative §2):
    #   >= auto_add   auto-add to tracking
    #   queue_min..auto_add   approval queue (shipment_candidates)
    #   < queue_min   ignore
    auto_add: float = 0.85
    queue_min: float = 0.50

    # Layer 3 — probe resolution. Log-only until probe_enabled is flipped.
    probe_enabled: bool = False
    probe_daily_budget: int = 20       # separate from poll budget; never starves polling
    probe_max_carriers: int = 2        # candidate carriers per ambiguous number
    probe_max_ship_age_days: int = 30  # recycled-number guard

    # Layer 4 — LLM fallback. Log-only until llm_enabled is flipped.
    llm_enabled: bool = False
    llm_model: str = "haiku"

    # Backfill sweep (backfill.py).
    backfill_window_days: int = 90
    backfill_chunk_size: int = 500
    backfill_default_max_hours: float = 4.0

    # Eval set (evalset.py).
    eval_export_limit: int = 200

    # Privacy: contacts at or above this privacy level are excluded
    # (matches the comms-recall rule; people.db privacy_level).
    privacy_min_level: int = 2

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "TrackingConfig":
        p = Path(path) if path else _config_path()
        raw: Dict[str, Any] = {}
        if yaml is not None and p.exists():
            try:
                raw = yaml.safe_load(p.read_text()) or {}
            except Exception:
                raw = {}
        detection = raw.get("detection", {}) or {}
        probe = raw.get("probe", {}) or {}
        llm = raw.get("llm", {}) or {}
        backfill = raw.get("backfill", {}) or {}
        privacy = raw.get("privacy", {}) or {}
        return cls(
            auto_add=float(detection.get("auto_add", cls.auto_add)),
            queue_min=float(detection.get("queue_min", cls.queue_min)),
            probe_enabled=bool(probe.get("enabled", cls.probe_enabled)),
            probe_daily_budget=int(probe.get("daily_budget", cls.probe_daily_budget)),
            probe_max_carriers=int(probe.get("max_carriers", cls.probe_max_carriers)),
            probe_max_ship_age_days=int(
                probe.get("max_ship_age_days", cls.probe_max_ship_age_days)
            ),
            llm_enabled=bool(llm.get("enabled", cls.llm_enabled)),
            llm_model=str(llm.get("model", cls.llm_model)),
            backfill_window_days=int(backfill.get("window_days", cls.backfill_window_days)),
            backfill_chunk_size=int(backfill.get("chunk_size", cls.backfill_chunk_size)),
            backfill_default_max_hours=float(
                backfill.get("default_max_hours", cls.backfill_default_max_hours)
            ),
            eval_export_limit=int(raw.get("eval_export_limit", cls.eval_export_limit)),
            privacy_min_level=int(privacy.get("min_level", cls.privacy_min_level)),
        )


def action_for(confidence: float, config: Optional[TrackingConfig] = None) -> str:
    """Map a confidence score to its action: auto_add | queue | ignore."""
    cfg = config or TrackingConfig()
    if confidence >= cfg.auto_add:
        return "auto_add"
    if confidence >= cfg.queue_min:
        return "queue"
    return "ignore"
