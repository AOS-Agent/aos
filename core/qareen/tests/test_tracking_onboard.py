"""Tests for the new-carrier onboarding pipeline (onboard.py) and the
``aos track`` CLI shell.

Covers: scaffold from _template, the fixture-validation gate (green against
the real ups/fedex fixtures, red with useful diffs against a deliberately
broken response_map), canary/graduate lifecycle gating in tracking_state,
manual add-number through the real detection pipeline, and CLI smoke via
direct main() calls — all against tmp carrier trees and tmp DBs. Nothing
here touches the real ~/.aos/data/*.db or writes into the real carriers/
tree.
"""

import importlib.machinery
import importlib.util
import shutil
import sys
from pathlib import Path

import pytest
import yaml

# Make the `qareen` package importable (package root is core/)
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from qareen.tracking import onboard, packs  # noqa: E402
from qareen.tracking.config import TrackingConfig  # noqa: E402
from qareen.tracking.store import ShipmentStore  # noqa: E402

REAL_CARRIERS = Path(__file__).resolve().parents[1] / "tracking" / "carriers"
CLI_PATH = Path(__file__).resolve().parents[2] / "bin" / "cli" / "aos-track"

UPS_NUMBER = "1Z999AA10123456784"  # doc-derived valid UPS number (passes ups_mod10)


def _store(tmp_path) -> ShipmentStore:
    return ShipmentStore(tmp_path / "qareen.db")


def _tmp_carriers(tmp_path, *slugs) -> Path:
    """A tmp carriers tree: _template plus copies of the requested real packs."""
    root = tmp_path / "carriers"
    root.mkdir()
    shutil.copytree(REAL_CARRIERS / "_template", root / "_template")
    for slug in slugs:
        shutil.copytree(REAL_CARRIERS / slug, root / slug)
    return root


def _load_cli():
    """Import core/bin/cli/aos-track (no .py extension) as a module."""
    loader = importlib.machinery.SourceFileLoader("aos_track_cli", str(CLI_PATH))
    spec = importlib.util.spec_from_loader("aos_track_cli", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


# ── Scaffold ─────────────────────────────────────────────────────────────


def test_scaffold_creates_lint_clean_pack(tmp_path):
    carriers = _tmp_carriers(tmp_path)
    store = _store(tmp_path)
    target = onboard.scaffold("purolator", carriers_dir=carriers, store=store)

    assert target == carriers / "purolator"
    assert (target / "manifest.yaml").is_file()
    assert (target / "fixtures").is_dir()

    manifest = yaml.safe_load((target / "manifest.yaml").read_text())
    assert manifest["carrier"] == "purolator"
    assert manifest["display_name"] == "Purolator"
    # The scaffolded pack must load through the real linter.
    pack = packs.load_pack(target)
    assert pack.slug == "purolator"

    # Scaffold records lifecycle state so detection filters can skip it.
    assert onboard.carrier_state(store, "purolator")["state"] == onboard.STATE_SCAFFOLDED


def test_scaffold_refuses_existing_and_bad_slugs(tmp_path):
    carriers = _tmp_carriers(tmp_path, "ups")
    with pytest.raises(onboard.OnboardError, match="already exists"):
        onboard.scaffold("ups", carriers_dir=carriers)
    with pytest.raises(onboard.OnboardError, match="slug"):
        onboard.scaffold("Not A Slug!", carriers_dir=carriers)


def test_checklist_mentions_pipeline_steps():
    text = onboard.checklist("purolator")
    for step in ("RESEARCH", "CREDENTIALS", "FIXTURE", "VALIDATE", "CANARY", "GRADUATE"):
        assert step in text
    assert "0.5–3 days" in text  # the honest framing stays front and center


# ── Fixture validation (green against real captured fixtures) ────────────


@pytest.mark.parametrize("slug", ["ups", "fedex", "usps", "dhl"])
def test_validate_real_packs_green(slug):
    report = onboard.validate_pack(slug)
    assert report.ok, report.render()
    track_fixtures = [f for f in report.fixtures if f.kind == "track"]
    assert track_fixtures, "expected at least one track fixture"
    assert all(f.events > 0 for f in track_fixtures)


def test_validate_canadapost_xml_via_mapper():
    report = onboard.validate_pack("canadapost")
    assert report.ok, report.render()
    track = [f for f in report.fixtures if f.kind == "track"]
    assert track and track[0].events > 0


def test_validate_error_fixtures_expect_no_events():
    report = onboard.validate_pack("ups")
    error_fixtures = [f for f in report.fixtures if f.kind == "error"]
    assert error_fixtures, "ups ships an error-envelope fixture"
    assert all(f.ok and f.events == 0 for f in error_fixtures)


# ── Fixture validation (red with a useful diff) ──────────────────────────


def _break_response_map(carriers: Path, slug: str) -> None:
    manifest_path = carriers / slug / "manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text())
    # Point events at a path that doesn't exist in the captured fixture.
    manifest["response_map"]["events"] = "$.no.such.array[*]"
    manifest_path.write_text(yaml.safe_dump(manifest))


def _drop_status_code(carriers: Path, slug: str, code: str) -> None:
    manifest_path = carriers / slug / "manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text())
    del manifest["status_map"][code]
    manifest_path.write_text(yaml.safe_dump(manifest))


def test_validate_red_when_events_path_broken(tmp_path):
    carriers = _tmp_carriers(tmp_path, "ups")
    _break_response_map(carriers, "ups")
    report = onboard.validate_pack("ups", carriers_dir=carriers)
    assert not report.ok
    text = report.render()
    assert "matched nothing" in text
    assert "✗" in text


def test_validate_red_names_unmapped_status_code(tmp_path):
    carriers = _tmp_carriers(tmp_path, "ups")
    _drop_status_code(carriers, "ups", "DL")  # Delivered, present in the fixture
    report = onboard.validate_pack("ups", carriers_dir=carriers)
    assert not report.ok
    text = report.render()
    assert "'DL'" in text
    assert "status_map" in text


def test_validate_red_without_fixtures(tmp_path):
    carriers = _tmp_carriers(tmp_path)
    onboard.scaffold("purolator", carriers_dir=carriers)
    report = onboard.validate_pack("purolator", carriers_dir=carriers)
    assert not report.ok
    assert "capture" in report.render().lower() or "fixtures" in report.render()


# ── Lifecycle: canary / graduate ─────────────────────────────────────────


def test_lifecycle_defaults_active_for_unrecorded_packs(tmp_path):
    store = _store(tmp_path)
    assert onboard.carrier_state(store, "ups")["state"] == onboard.STATE_ACTIVE
    rows = onboard.lifecycle(store)
    by_slug = {r["slug"]: r["state"] for r in rows}
    assert by_slug["ups"] == onboard.STATE_ACTIVE
    assert "_template" not in by_slug


def test_canary_and_graduate_gate_on_validation(tmp_path):
    carriers = _tmp_carriers(tmp_path, "ups")
    store = _store(tmp_path)
    onboard.scaffold("purolator", carriers_dir=carriers, store=store)

    # No fixtures → canary refuses (the honest gate).
    with pytest.raises(onboard.OnboardError, match="RED"):
        onboard.canary("purolator", store, carriers_dir=carriers)
    # --force bypasses, and records it.
    record = onboard.canary("purolator", store, carriers_dir=carriers, force=True)
    assert record["state"] == onboard.STATE_CANARY
    assert onboard.carrier_state(store, "purolator")["state"] == onboard.STATE_CANARY

    # Green pack: graduate passes validation and goes active.
    record = onboard.graduate("ups", store, carriers_dir=carriers)
    assert record["state"] == onboard.STATE_ACTIVE
    assert onboard.carrier_state(store, "ups")["state"] == onboard.STATE_ACTIVE


def test_graduate_refuses_red_pack(tmp_path):
    carriers = _tmp_carriers(tmp_path, "ups")
    store = _store(tmp_path)
    _break_response_map(carriers, "ups")
    with pytest.raises(onboard.OnboardError, match="RED"):
        onboard.graduate("ups", store, carriers_dir=carriers)
    record = onboard.graduate("ups", store, carriers_dir=carriers, force=True)
    assert "force" in record["note"]


# ── Manual add ───────────────────────────────────────────────────────────


def test_add_number_with_explicit_carrier(tmp_path):
    store = _store(tmp_path)
    result = onboard.add_number(
        store, number=UPS_NUMBER, carrier="ups", config=TrackingConfig()
    )
    assert len(result["shipments"]) == 1
    rec = result["shipments"][0]
    assert rec["action"] == "added"
    row = store.get_shipment_row(rec["shipment_id"])
    assert row["tracking_number"] == UPS_NUMBER
    assert row["carrier"] == "ups"
    assert row["source"] == "manual"

    # Re-adding merges into the same shipment (store dedup). The re-add uses
    # a non-canonical form (lowercase) so canonicalization is exercised; a
    # spaced variant would be phone-shaped and trips privacy-scan.
    again = onboard.add_number(store, number="1z999aa10123456784", carrier="ups",
                               config=TrackingConfig())
    assert again["shipments"][0]["shipment_id"] == rec["shipment_id"]
    assert again["shipments"][0]["action"] == "merged"


def test_add_number_rejects_invalid_number_for_carrier(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(onboard.OnboardError, match="not a valid"):
        onboard.add_number(store, number="GARBAGE123", carrier="ups",
                           config=TrackingConfig())
    with pytest.raises(onboard.OnboardError, match="unknown carrier"):
        onboard.add_number(store, number=UPS_NUMBER, carrier="acme-freight",
                           config=TrackingConfig())


def test_add_number_text_blob_runs_detection(tmp_path):
    """A pasted blob with a tracking URL → high-confidence auto-add."""
    store = _store(tmp_path)
    blob = "Your package shipped! Track it: https://www.ups.com/track?tracknum=%s" % UPS_NUMBER
    result = onboard.add_number(store, text=blob, config=TrackingConfig())
    assert result["shipments"], result
    assert result["shipments"][0]["carrier"] == "ups"


def test_add_number_bare_number_queues_for_approval(tmp_path):
    """A bare number with no URL context lands in the approval queue band."""
    store = _store(tmp_path)
    result = onboard.add_number(store, number=UPS_NUMBER, config=TrackingConfig())
    assert not result["shipments"]
    assert len(result["candidates"]) == 1
    queued = store.peek_candidates()
    assert len(queued) == 1
    assert queued[0]["candidate"]["tracking_number"] == UPS_NUMBER


def test_list_and_detail(tmp_path):
    store = _store(tmp_path)
    rec = onboard.add_number(store, number=UPS_NUMBER, carrier="ups",
                             config=TrackingConfig())["shipments"][0]

    rows = onboard.list_shipments(store)
    assert [r["id"] for r in rows] == [rec["shipment_id"]]
    assert onboard.list_shipments(store, status="delivered") == []

    detail = onboard.shipment_detail(store, rec["shipment_id"])
    assert detail["shipment"]["tracking_number"] == UPS_NUMBER
    assert detail["numbers"][0]["role"] == "primary"
    assert onboard.shipment_detail(store, "shp_nope") is None


# ── CLI smoke (direct main() calls, tmp db + tmp carriers) ───────────────


def test_cli_scaffold_validate_graduate_flow(tmp_path, capsys):
    cli = _load_cli()
    carriers = _tmp_carriers(tmp_path, "ups")
    db = str(tmp_path / "qareen.db")
    base = ["--db", db, "--carriers-dir", str(carriers)]

    assert cli.main(base + ["add", "purolator"]) == 0
    assert (carriers / "purolator" / "manifest.yaml").is_file()
    out = capsys.readouterr().out
    assert "GRADUATE" in out

    # Validate green pack → exit 0; broken pack → exit 1.
    assert cli.main(base + ["validate", "ups"]) == 0
    assert "GREEN" in capsys.readouterr().out
    _break_response_map(carriers, "ups")
    assert cli.main(base + ["validate", "ups"]) == 1
    assert "RED" in capsys.readouterr().out

    # Graduate refuses the red pack, force overrides.
    assert cli.main(base + ["graduate", "ups"]) == 1
    assert cli.main(base + ["graduate", "ups", "--force"]) == 0

    # Lifecycle overview lists every pack with its state.
    assert cli.main(base + ["status"]) == 0
    out = capsys.readouterr().out
    assert "purolator" in out and "scaffolded" in out


def test_cli_add_number_list_show(tmp_path, capsys):
    cli = _load_cli()
    db = str(tmp_path / "qareen.db")

    assert cli.main(["--db", db, "add-number", UPS_NUMBER, "--carrier", "ups"]) == 0
    out = capsys.readouterr().out
    assert "Added shipment" in out
    shipment_id = out.split("Added shipment ", 1)[1].split(" ", 1)[0]

    assert cli.main(["--db", db, "list", "--status", "active"]) == 0
    assert UPS_NUMBER in capsys.readouterr().out

    assert cli.main(["--db", db, "show", shipment_id]) == 0
    assert UPS_NUMBER in capsys.readouterr().out

    # Unknown shipment id → exit 1.
    assert cli.main(["--db", db, "show", "shp_nope"]) == 1

    # Invalid number for a carrier → friendly error, exit 1.
    assert cli.main(["--db", db, "add-number", "NOPE", "--carrier", "ups"]) == 1
    assert "not a valid" in capsys.readouterr().err
