"""eval_gate — frozen-dataset judge harness for the intelligence loop.

The loop engine classifies operator messages into friction labels
(correction / frustration / overreach / retry / none). Before any change
to the judge prompt or model ships, it must clear this gate against a
frozen, hand-labeled dataset — so a prompt tweak that looks better on a
handful of examples can't silently regress on the rest.

Dataset layout (default: ~/vault/knowledge/references/loop-eval-v1/,
override with AOS_LOOP_EVAL_DIR):

    messages.jsonl   one row per message:
                     {"session", "idx", "text", "prev_assistant_snippet"}
    labels.jsonl     one row per message, joined on (session, idx):
                     {"session", "idx", "label", "machine_text", "confidence"}
    MANIFEST.json    {"version", "frozen_at",
                      "sha256": {"messages.jsonl": ..., "labels.jsonl": ...},
                      "counts": {...}}

`verify_frozen` recomputes sha256 of both jsonl files and raises if either
has drifted from MANIFEST.json — the "frozen" guarantee. `run_gate` runs a
judge over every message and scores it against pass/fail thresholds.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

LABELS = ("correction", "frustration", "overreach", "retry", "none")
FRICTION_LABELS = tuple(l for l in LABELS if l != "none")

DEFAULT_THRESHOLDS = {
    "machine_fp_max": 0,
    "binary_precision_min": 0.80,
    "binary_recall_min": 0.70,
}

# judge(text, prev_assistant_snippet) -> {"label": str, "machine_text": bool}
Judge = Callable[[str, str | None], Awaitable[dict[str, Any]]]


class FrozenDatasetError(Exception):
    """Raised when the eval dataset doesn't match its MANIFEST.json."""


def default_dataset_dir() -> Path:
    override = os.environ.get("AOS_LOOP_EVAL_DIR")
    if override:
        return Path(override)
    return Path.home() / "vault" / "knowledge" / "references" / "loop-eval-v1"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _load_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def verify_frozen(dataset_dir: Path) -> None:
    """Recompute sha256 of messages.jsonl and labels.jsonl and compare
    against MANIFEST.json. Raises FrozenDatasetError on any mismatch or
    missing file — this is the guarantee that the eval set can't drift
    silently.
    """
    dataset_dir = Path(dataset_dir)
    manifest_path = dataset_dir / "MANIFEST.json"
    if not manifest_path.exists():
        raise FrozenDatasetError(
            f"No MANIFEST.json in {dataset_dir} — dataset not frozen "
            f"(run `loop-eval freeze <src_dir>` first)."
        )

    manifest = json.loads(manifest_path.read_text())
    expected = manifest.get("sha256", {})

    for fname in ("messages.jsonl", "labels.jsonl"):
        fpath = dataset_dir / fname
        if not fpath.exists():
            raise FrozenDatasetError(f"Missing {fname} in {dataset_dir}")
        want = expected.get(fname)
        if not want:
            raise FrozenDatasetError(f"MANIFEST.json has no sha256 entry for {fname}")
        got = _sha256_file(fpath)
        if got != want:
            raise FrozenDatasetError(
                f"{fname} has been modified since freeze: "
                f"expected sha256={want[:12]}… got={got[:12]}…"
            )


@dataclass
class GateResult:
    passed: bool
    metrics: dict[str, Any]
    failures: list[dict[str, Any]] = field(default_factory=list)


def _precision_recall(tp: int, fp: int, fn: int) -> tuple[float, float]:
    precision = tp / (tp + fp) if (tp + fp) > 0 else 1.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 1.0
    return precision, recall


async def run_gate(
    judge: Judge,
    dataset_dir: Path | None = None,
    thresholds: dict[str, float] | None = None,
) -> GateResult:
    """Run `judge` over every message in the frozen dataset and score it.

    Bounded concurrency of 4 (a plain semaphore over asyncio.gather —
    falling back to effectively sequential execution is fine, the point
    is to not fire 500 concurrent LLM calls).
    """
    dataset_dir = Path(dataset_dir) if dataset_dir else default_dataset_dir()
    verify_frozen(dataset_dir)
    thresholds = {**DEFAULT_THRESHOLDS, **(thresholds or {})}

    messages = _load_jsonl(dataset_dir / "messages.jsonl")
    labels = _load_jsonl(dataset_dir / "labels.jsonl")
    label_by_key = {(row["session"], row["idx"]): row for row in labels}

    sem = asyncio.Semaphore(4)

    async def _judge_one(msg: dict) -> tuple[dict, dict | None, dict]:
        async with sem:
            got = await judge(msg["text"], msg.get("prev_assistant_snippet"))
        key = (msg["session"], msg["idx"])
        return msg, label_by_key.get(key), got

    graded = await asyncio.gather(*[_judge_one(m) for m in messages])

    per_class = {l: {"tp": 0, "fp": 0, "fn": 0, "support": 0} for l in LABELS}
    binary = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
    machine_fp = 0
    failures: list[dict[str, Any]] = []
    evaluated = 0
    skipped_unlabeled = 0

    for msg, label_row, got in graded:
        if label_row is None:
            skipped_unlabeled += 1
            continue
        evaluated += 1

        expected = label_row["label"]
        predicted = got.get("label")

        per_class.setdefault(expected, {"tp": 0, "fp": 0, "fn": 0, "support": 0})
        per_class.setdefault(predicted, {"tp": 0, "fp": 0, "fn": 0, "support": 0})
        per_class[expected]["support"] += 1

        if predicted == expected:
            per_class[expected]["tp"] += 1
        else:
            per_class[expected]["fn"] += 1
            per_class[predicted]["fp"] += 1

        expected_friction = expected != "none"
        predicted_friction = predicted != "none"
        if expected_friction and predicted_friction:
            binary["tp"] += 1
        elif expected_friction and not predicted_friction:
            binary["fn"] += 1
        elif not expected_friction and predicted_friction:
            binary["fp"] += 1
        else:
            binary["tn"] += 1

        if label_row.get("machine_text") and predicted_friction:
            machine_fp += 1

        if predicted != expected and len(failures) < 20:
            failures.append({
                "session": msg["session"],
                "idx": msg["idx"],
                "expected": expected,
                "got": predicted,
            })

    per_class_metrics = {}
    for label, counts in per_class.items():
        precision, recall = _precision_recall(counts["tp"], counts["fp"], counts["fn"])
        per_class_metrics[label] = {
            **counts,
            "precision": precision,
            "recall": recall,
        }

    binary_precision, binary_recall = _precision_recall(binary["tp"], binary["fp"], binary["fn"])

    metrics = {
        "evaluated": evaluated,
        "skipped_unlabeled": skipped_unlabeled,
        "per_class": per_class_metrics,
        "binary": {
            **binary,
            "precision": binary_precision,
            "recall": binary_recall,
        },
        "machine_fp": machine_fp,
        "thresholds": thresholds,
    }

    passed = (
        machine_fp <= thresholds["machine_fp_max"]
        and binary_precision >= thresholds["binary_precision_min"]
        and binary_recall >= thresholds["binary_recall_min"]
    )

    return GateResult(passed=passed, metrics=metrics, failures=failures)


def format_report(result: GateResult) -> str:
    """Compact markdown report: verdict, binary summary, per-class table,
    and a sample of misclassifications.
    """
    m = result.metrics
    binary = m["binary"]
    verdict = "PASS" if result.passed else "FAIL"

    lines = [
        f"## Loop eval gate — {verdict}",
        "",
        f"Evaluated {m['evaluated']} messages"
        + (f" ({m['skipped_unlabeled']} skipped, no label)" if m.get("skipped_unlabeled") else "")
        + ".",
        "",
        "| metric | value | threshold |",
        "|---|---|---|",
        f"| machine-text false positives | {m['machine_fp']} | <= {m['thresholds']['machine_fp_max']} |",
        f"| binary precision (friction vs none) | {binary['precision']:.2f} | >= {m['thresholds']['binary_precision_min']:.2f} |",
        f"| binary recall (friction vs none) | {binary['recall']:.2f} | >= {m['thresholds']['binary_recall_min']:.2f} |",
        "",
        "| label | support | precision | recall |",
        "|---|---|---|---|",
    ]
    for label in LABELS:
        c = m["per_class"].get(label, {"support": 0, "precision": 1.0, "recall": 1.0})
        lines.append(f"| {label} | {c['support']} | {c['precision']:.2f} | {c['recall']:.2f} |")

    if result.failures:
        lines += ["", f"### Misclassifications (showing {len(result.failures)})", "",
                   "| session | idx | expected | got |", "|---|---|---|---|"]
        for f in result.failures:
            lines.append(f"| {f['session']} | {f['idx']} | {f['expected']} | {f['got']} |")

    return "\n".join(lines)
