"""Tests for the backfill sweep and the eval-set export/report.

Uses a tmp comms.db (messages table only) and a fake duck-typed store.
Fixtures packs are tmp_path directories — no real carrier packs involved.
"""

import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import yaml

# Make the `qareen` package importable (package root is core/)
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from qareen.tracking import backfill, evalset  # noqa: E402
from qareen.tracking.packs import load_packs  # noqa: E402

UPS_NUMBER = "1Z999AA10123456784"


# ── fixtures ─────────────────────────────────────────────────────────────


def _write_pack(carriers_dir: Path, slug: str, patterns, url_templates=None) -> None:
    manifest = {
        "carrier": slug,
        "display_name": slug.title(),
        "auth": {"model": "none"},
        "endpoints": {"base": "https://api.example.com", "track": "https://api.example.com/t/{number}"},
        "tracking": {"patterns": patterns, "check_digit": None},
        "url_templates": url_templates or [],
        "capabilities": {"edd": True, "pod": False, "push": False},
        "status_map": {"IT": "in_transit", "DL": "delivered"},
        "response_map": {
            "events": "$.events[*]",
            "event_fields": {"code": "$.code", "description": "$.desc"},
        },
        "rate_limits": {"requests_per_day": 100, "min_interval_seconds": 1},
        "retention": {"delete_days_after_delivery": None},
    }
    pack_dir = carriers_dir / slug
    pack_dir.mkdir(parents=True)
    (pack_dir / "manifest.yaml").write_text(yaml.safe_dump(manifest))


@pytest.fixture()
def packs(tmp_path):
    carriers = tmp_path / "carriers"
    _write_pack(
        carriers, "ups", ["1Z[0-9A-Z]{16}"],
        url_templates=["https://www.ups.com/track?tracknum={number}"],
    )
    return load_packs(carriers)


def _make_comms_db(path: Path, rows) -> Path:
    """rows: list of (channel, direction, sender_id, content, timestamp)."""
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE messages ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " channel TEXT, direction TEXT, sender_id TEXT, recipient_id TEXT,"
        " content TEXT, timestamp TEXT, thread_id TEXT, person_id TEXT,"
        " conversation_id TEXT, channel_metadata TEXT)"
    )
    for channel, direction, sender, content, ts in rows:
        conn.execute(
            "INSERT INTO messages (channel, direction, sender_id, content, timestamp,"
            " conversation_id) VALUES (?,?,?,?,?,?)",
            (channel, direction, sender, content, ts, "conv-%s" % sender),
        )
    conn.commit()
    conn.close()
    return path


class FakeStore:
    def __init__(self):
        self.shipments = []
        self.candidates = []
        self.state = {}
        self.eval_rows = []

    def get_priors(self, domain):
        return {}

    def add_shipment(self, tracking_number, carrier, sources, confidence, layer,
                     merchant_domain=None):
        self.shipments.append({"tracking_number": tracking_number, "carrier": carrier})

    def enqueue_candidate(self, candidate_dict, layer=None, confidence=None):
        self.candidates.append(candidate_dict)

    def get_state(self, key):
        return self.state.get(key)

    def set_state(self, key, value):
        self.state[key] = value

    def add_eval_candidate(self, row):
        self.eval_rows.append(dict(row))

    def iter_eval_rows(self):
        return list(self.eval_rows)


def _recent(days_ago=1):
    return (datetime.now() - timedelta(days=days_ago)).isoformat()


@pytest.fixture()
def comms_db(tmp_path):
    return _make_comms_db(tmp_path / "comms.db", [
        # id 1 — old, outside the 90-day window
        ("email", "in", "old@merchant.example", "tracking %s" % UPS_NUMBER,
         (datetime.now() - timedelta(days=120)).isoformat()),
        # id 2 — recent, tracking URL (auto-add)
        ("email", "in", "noreply@merchant.example",
         "shipped! https://www.ups.com/track?tracknum=%s" % UPS_NUMBER, _recent(2)),
        # id 3 — recent, bare number (queue band)
        ("imessage", "in", "+15551234567", "yo track %s" % UPS_NUMBER, _recent(1)),
        # id 4 — outbound, skipped
        ("email", "out", "me", "here is the number %s" % UPS_NUMBER, _recent(1)),
        # id 5 — recent, no tracking content
        ("imessage", "in", "+15559998888", "what's for dinner", _recent(0)),
    ])


# ── backfill ─────────────────────────────────────────────────────────────


def test_dry_run_reports_but_writes_nothing(comms_db, packs):
    store = FakeStore()
    report = backfill.run_backfill(comms_db, store=store, packs=packs, write=False)

    assert report["write"] is False
    assert report["scanned"] == 3          # ids 2, 3, 5 (old + outbound excluded)
    assert report["skipped_from_me"] == 1
    assert report["skipped_old"] == 1
    assert report["candidates"] == 2       # URL hit + body hit
    assert report["by_layer"] == {"url": 1, "body": 1}
    assert report["actions"]["auto_add"] == 1
    assert report["actions"]["queue"] == 1
    # dry run: no persistence, no watermark movement
    assert store.shipments == [] and store.candidates == []
    assert store.get_state(backfill.WATERMARK_KEY) is None
    assert report["watermark_after"] == report["watermark_before"]


def test_write_persists_and_advances_watermark(comms_db, packs):
    store = FakeStore()
    report = backfill.run_backfill(comms_db, store=store, packs=packs, write=True)

    assert len(store.shipments) == 1       # URL detection → auto-add
    assert store.shipments[0]["tracking_number"] == UPS_NUMBER
    assert len(store.candidates) == 1      # body detection → queue
    assert store.get_state(backfill.WATERMARK_KEY) == str(report["high_water"])
    assert report["watermark_after"] == report["high_water"]


def test_watermark_resume_skips_scanned_history(comms_db, packs, tmp_path):
    store = FakeStore()
    first = backfill.run_backfill(comms_db, store=store, packs=packs, write=True)
    assert first["completed"] is True

    shipments_after_first = len(store.shipments)
    # Add one NEW message (id 6) after the first sweep.
    conn = sqlite3.connect(str(comms_db))
    conn.execute(
        "INSERT INTO messages (channel, direction, sender_id, content, timestamp,"
        " conversation_id) VALUES (?,?,?,?,?,?)",
        ("email", "in", "two@merchant.example",
         "another https://www.ups.com/track?tracknum=1Z888AA10123456784",
         _recent(0), "conv-new"),
    )
    conn.commit()
    conn.close()

    second = backfill.run_backfill(comms_db, store=store, packs=packs, write=True)
    assert second["scanned"] == 1          # only the new message
    assert len(store.shipments) == shipments_after_first + 1


def test_incomplete_run_does_not_advance_watermark(comms_db, packs):
    store = FakeStore()
    # max_hours=0 → budget exhausted before the first chunk
    report = backfill.run_backfill(
        comms_db, store=store, packs=packs, write=True, max_hours=0.0,
    )
    assert report["completed"] is False
    assert store.get_state(backfill.WATERMARK_KEY) is None


def test_backfill_render_report_smoke(comms_db, packs):
    report = backfill.run_backfill(comms_db, store=FakeStore(), packs=packs, write=False)
    text = backfill.render_report(report)
    assert "DRY-RUN" in text
    assert "scanned: 3" in text


def test_cli_dry_run_and_write(comms_db, packs, monkeypatch, capsys):
    monkeypatch.setattr(backfill, "load_packs", lambda: packs)
    monkeypatch.setattr(backfill, "_open_default_store", lambda: FakeStore())

    rc = backfill.main(["--comms-db", str(comms_db)])
    assert rc == 0
    assert "DRY-RUN" in capsys.readouterr().out

    rc = backfill.main(["--comms-db", str(comms_db), "--write"])
    assert rc == 0
    assert "WRITE" in capsys.readouterr().out

    rc = backfill.main(["--comms-db", str(comms_db.parent / "missing.db")])
    assert rc == 1


# ── evalset ──────────────────────────────────────────────────────────────


def test_evalset_export_extracts_candidates_read_only(comms_db, packs):
    store = FakeStore()
    exported = evalset.export_candidates(comms_db, store, packs=packs, limit=200)

    assert exported == store.eval_rows
    assert len(exported) == 2  # URL + body detections from recent inbound mail
    layers = {row["layer"] for row in exported}
    assert layers == {"url", "body"}
    assert all(row["label"] is None for row in exported)
    assert all(row["tracking_number"] == UPS_NUMBER for row in exported)


def test_evalset_export_respects_limit(comms_db, packs):
    store = FakeStore()
    exported = evalset.export_candidates(comms_db, store, packs=packs, limit=1)
    assert len(exported) == 1


def test_evalset_metrics_math():
    rows = [
        {"layer": "url", "label": "correct"},
        {"layer": "url", "label": "correct"},
        {"layer": "url", "label": "incorrect"},
        {"layer": "url", "label": "missed"},
        {"layer": "body", "label": "correct"},
        {"layer": "body", "label": "incorrect"},
        {"layer": "body", "label": "incorrect"},
        {"layer": "body", "label": None},       # unlabeled — excluded
        {"layer": "probe", "label": "missed"},  # fn only: no precision, recall 0
    ]
    metrics = evalset.compute_metrics(rows)

    assert metrics["url"]["precision"] == pytest.approx(2 / 3)
    assert metrics["url"]["recall"] == pytest.approx(2 / 3)
    assert metrics["body"]["precision"] == pytest.approx(1 / 3)
    assert metrics["body"]["recall"] == pytest.approx(1.0)
    assert metrics["body"]["unlabeled"] == 1
    assert metrics["probe"]["precision"] is None
    assert metrics["probe"]["recall"] == pytest.approx(0.0)


def test_evalset_render_report_smoke():
    text = evalset.render_report([
        {"layer": "url", "label": "correct"},
        {"layer": "url", "label": "incorrect"},
    ])
    assert "url" in text
    assert "0.500" in text
    assert "no eval rows" in evalset.render_report([])


def test_evalset_cli_report(comms_db, packs, monkeypatch, capsys):
    store = FakeStore()
    exported = evalset.export_candidates(comms_db, store, packs=packs)
    for row in store.eval_rows:
        row["label"] = "correct"

    monkeypatch.setattr(evalset, "_open_default_store", lambda: store)
    rc = evalset.main(["--report"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "precision" in out
    assert "url" in out

    monkeypatch.setattr(evalset, "_open_default_store", lambda: store)
    monkeypatch.setattr(evalset, "load_packs", lambda: packs)
    rc = evalset.main(["--export", "--comms-db", str(comms_db)])
    assert rc == 0
    assert "exported" in capsys.readouterr().out
    assert len(store.eval_rows) == 2 * len(exported)  # second export appends
