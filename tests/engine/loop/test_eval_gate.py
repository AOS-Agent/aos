"""Tests for the loop eval gate harness.

No LLM calls here — every "judge" is a fake dict lookup keyed by message
text, and every dataset is a tiny fixture built fresh in tmp_path. This
exercises the frozen-dataset guarantee and the scoring math in isolation
from anything that talks to a model.

Import note: this must be `from core.engine.loop import eval_gate`, not
`engine.loop`. conftest.py puts core/engine/work on sys.path for the work
fixtures, and core/engine/work/engine.py shadows the `engine` namespace
package — `import engine.loop` would silently resolve to the wrong thing.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from core.engine.loop import eval_gate

# ── fixtures ─────────────────────────────────────────────────────────────


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def _freeze_dataset(dataset_dir: Path, messages: list[dict], labels: list[dict]) -> Path:
    """Build messages.jsonl + labels.jsonl + MANIFEST.json — the same shape
    `loop-eval freeze` produces, built by hand here to keep the test
    independent of the CLI script.
    """
    dataset_dir.mkdir(parents=True, exist_ok=True)
    messages_path = dataset_dir / "messages.jsonl"
    labels_path = dataset_dir / "labels.jsonl"
    _write_jsonl(messages_path, messages)
    _write_jsonl(labels_path, labels)

    manifest = {
        "version": "test",
        "frozen_at": "2026-07-21T00:00:00+00:00",
        "sha256": {
            "messages.jsonl": _sha256(messages_path),
            "labels.jsonl": _sha256(labels_path),
        },
        "counts": {"messages": len(messages), "labels": len(labels)},
    }
    (dataset_dir / "MANIFEST.json").write_text(json.dumps(manifest, indent=2))
    return dataset_dir


def _msg(session: str, idx: int, prev: str | None = None) -> dict:
    return {
        "session": session,
        "idx": idx,
        "text": f"msg-{session}-{idx}",
        "prev_assistant_snippet": prev,
    }


def _label(session: str, idx: int, label: str, *, machine_text: bool = False, confidence: float = 1.0) -> dict:
    return {
        "session": session,
        "idx": idx,
        "label": label,
        "machine_text": machine_text,
        "confidence": confidence,
    }


def _dict_judge(predictions: dict[str, dict]):
    """A fake judge: async callable that looks up (text) in a plain dict.
    Mirrors the real judge signature (text, prev_snippet) -> result dict.
    """
    async def judge(text: str, prev_snippet: str | None) -> dict:
        return predictions[text]
    return judge


# ── verify_frozen ────────────────────────────────────────────────────────


def test_verify_frozen_passes_on_untouched_dataset(tmp_path):
    ds = _freeze_dataset(
        tmp_path / "ds",
        [_msg("s1", 0)],
        [_label("s1", 0, "none")],
    )
    eval_gate.verify_frozen(ds)  # must not raise


def test_verify_frozen_catches_tampering(tmp_path):
    ds = _freeze_dataset(
        tmp_path / "ds",
        [_msg("s1", 0)],
        [_label("s1", 0, "none")],
    )
    eval_gate.verify_frozen(ds)  # sanity: clean first

    # Tamper with messages.jsonl after freezing, without touching MANIFEST.json.
    (ds / "messages.jsonl").write_text(json.dumps(_msg("s1", 0)) + "\n" + json.dumps(_msg("s1", 1)) + "\n")

    with pytest.raises(eval_gate.FrozenDatasetError):
        eval_gate.verify_frozen(ds)


def test_verify_frozen_missing_manifest(tmp_path):
    ds = tmp_path / "ds"
    ds.mkdir()
    _write_jsonl(ds / "messages.jsonl", [_msg("s1", 0)])
    _write_jsonl(ds / "labels.jsonl", [_label("s1", 0, "none")])

    with pytest.raises(eval_gate.FrozenDatasetError):
        eval_gate.verify_frozen(ds)


# ── run_gate: pass/fail behavior ────────────────────────────────────────


def test_perfect_judge_passes(tmp_path):
    messages = [
        _msg("s1", 0), _msg("s1", 1), _msg("s1", 2),
        _msg("s2", 0), _msg("s2", 1),
    ]
    labels = [
        _label("s1", 0, "none"),
        _label("s1", 1, "correction"),
        _label("s1", 2, "frustration"),
        _label("s2", 0, "none", machine_text=True),
        _label("s2", 1, "overreach"),
    ]
    ds = _freeze_dataset(tmp_path / "ds", messages, labels)

    predictions = {
        "msg-s1-0": {"label": "none", "machine_text": False},
        "msg-s1-1": {"label": "correction", "machine_text": False},
        "msg-s1-2": {"label": "frustration", "machine_text": False},
        "msg-s2-0": {"label": "none", "machine_text": True},
        "msg-s2-1": {"label": "overreach", "machine_text": False},
    }
    judge = _dict_judge(predictions)

    result = _run(eval_gate.run_gate(judge, dataset_dir=ds))

    assert result.passed is True
    assert result.metrics["machine_fp"] == 0
    assert result.metrics["binary"]["precision"] == pytest.approx(1.0)
    assert result.metrics["binary"]["recall"] == pytest.approx(1.0)
    assert result.failures == []


def test_machine_text_flagged_as_friction_hard_fails(tmp_path):
    messages = [_msg("s1", 0), _msg("s2", 0)]
    labels = [
        _label("s1", 0, "correction"),
        _label("s2", 0, "none", machine_text=True),
    ]
    ds = _freeze_dataset(tmp_path / "ds", messages, labels)

    predictions = {
        "msg-s1-0": {"label": "correction", "machine_text": False},
        # machine_text=True row gets flagged as friction — must hard-fail
        # the gate regardless of how good the rest of the metrics look.
        "msg-s2-0": {"label": "overreach", "machine_text": True},
    }
    judge = _dict_judge(predictions)

    result = _run(eval_gate.run_gate(judge, dataset_dir=ds))

    assert result.metrics["machine_fp"] == 1
    assert result.passed is False


def test_thresholds_are_overridable(tmp_path):
    # Same as the machine-fp case, but with machine_fp_max relaxed — the
    # gate should now pass if the rest of the metrics clear the bar.
    messages = [_msg("s1", 0), _msg("s2", 0)]
    labels = [
        _label("s1", 0, "correction"),
        _label("s2", 0, "none", machine_text=True),
    ]
    ds = _freeze_dataset(tmp_path / "ds", messages, labels)

    predictions = {
        "msg-s1-0": {"label": "correction", "machine_text": False},
        "msg-s2-0": {"label": "overreach", "machine_text": True},
    }
    judge = _dict_judge(predictions)

    result = _run(eval_gate.run_gate(
        judge, dataset_dir=ds,
        thresholds={"machine_fp_max": 1, "binary_precision_min": 0.0, "binary_recall_min": 0.0},
    ))

    assert result.metrics["machine_fp"] == 1
    assert result.passed is True


# ── precision / recall math ─────────────────────────────────────────────


def test_precision_recall_hand_computed(tmp_path):
    messages = [_msg("s", i) for i in range(5)]
    labels = [
        _label("s", 0, "correction"),
        _label("s", 1, "correction"),
        _label("s", 2, "none"),
        _label("s", 3, "frustration"),
        _label("s", 4, "none"),
    ]
    ds = _freeze_dataset(tmp_path / "ds", messages, labels)

    # idx0: correction -> correction   (correct)
    # idx1: correction -> none         (miss)
    # idx2: none       -> correction   (false alarm)
    # idx3: frustration -> frustration (correct)
    # idx4: none       -> none         (correct)
    predictions = {
        "msg-s-0": {"label": "correction", "machine_text": False},
        "msg-s-1": {"label": "none", "machine_text": False},
        "msg-s-2": {"label": "correction", "machine_text": False},
        "msg-s-3": {"label": "frustration", "machine_text": False},
        "msg-s-4": {"label": "none", "machine_text": False},
    }
    judge = _dict_judge(predictions)

    result = _run(eval_gate.run_gate(
        judge, dataset_dir=ds,
        thresholds={"binary_precision_min": 0.0, "binary_recall_min": 0.0},
    ))

    m = result.metrics
    # correction: tp=1 (idx0), fp=1 (idx2 mispredicted as correction), fn=1 (idx1 missed)
    assert m["per_class"]["correction"]["precision"] == pytest.approx(0.5)
    assert m["per_class"]["correction"]["recall"] == pytest.approx(0.5)
    # frustration: perfect
    assert m["per_class"]["frustration"]["precision"] == pytest.approx(1.0)
    assert m["per_class"]["frustration"]["recall"] == pytest.approx(1.0)
    # none: tp=1 (idx4), fp=1 (idx1 predicted none, expected correction), fn=1 (idx2 missed)
    assert m["per_class"]["none"]["precision"] == pytest.approx(0.5)
    assert m["per_class"]["none"]["recall"] == pytest.approx(0.5)

    # binary friction-vs-none: tp=2 (idx0,idx3), fp=1 (idx2), fn=1 (idx1), tn=1 (idx4)
    assert m["binary"]["tp"] == 2
    assert m["binary"]["fp"] == 1
    assert m["binary"]["fn"] == 1
    assert m["binary"]["tn"] == 1
    assert m["binary"]["precision"] == pytest.approx(2 / 3)
    assert m["binary"]["recall"] == pytest.approx(2 / 3)

    assert len(result.failures) == 2
    got_pairs = {(f["session"], f["idx"]) for f in result.failures}
    assert got_pairs == {("s", 1), ("s", 2)}


# ── format_report ────────────────────────────────────────────────────────


def test_format_report_renders_pass_and_fail(tmp_path):
    ds = _freeze_dataset(
        tmp_path / "ds",
        [_msg("s1", 0)],
        [_label("s1", 0, "none")],
    )

    passing = _run(eval_gate.run_gate(_dict_judge({"msg-s1-0": {"label": "none", "machine_text": False}}), dataset_dir=ds))
    report = eval_gate.format_report(passing)
    assert isinstance(report, str)
    assert "PASS" in report
    assert "machine-text false positives" in report
    assert "| label | support | precision | recall |" in report

    failing = _run(eval_gate.run_gate(_dict_judge({"msg-s1-0": {"label": "correction", "machine_text": False}}), dataset_dir=ds))
    failing_report = eval_gate.format_report(failing)
    assert "FAIL" in failing_report
    assert "Misclassifications" in failing_report


# ── async helper ─────────────────────────────────────────────────────────


def _run(coro):
    import asyncio
    return asyncio.run(coro)
