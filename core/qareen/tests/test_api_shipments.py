"""Tests for the Auto Tracker API router (qareen.api.shipments).

Exercises the router through a FastAPI TestClient with a tmp_path
ShipmentStore (never the real ~/.aos/data/qareen.db) and a fake EventBus
capturing emits. Covers: list/summary, detail, manual add (number + paste
box), patch, candidate confirm/reject lifecycle with detection-prior
writeback, domain rules, eval labeling, and the UPS Track Alert webhook
(good secret / bad secret / bad payload / unknown number / event mapping).
"""

import sys
import types
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from qareen.api import shipments as shipments_api  # noqa: E402
from qareen.tracking.config import TrackingConfig  # noqa: E402
from qareen.tracking.packs import load_packs  # noqa: E402
from qareen.tracking.store import ShipmentStore  # noqa: E402

UPS_NUMBER = "1Z999AA10123456784"  # passes ups_mod10 (pack fixture number)


class FakeBus:
    def __init__(self):
        self.events = []

    async def emit(self, event):
        self.events.append(event)

    def types(self):
        return [e.event_type for e in self.events]


@pytest.fixture()
def env(tmp_path, monkeypatch):
    store = ShipmentStore(db_path=tmp_path / "qareen.db")
    packs = load_packs()
    config = TrackingConfig()
    monkeypatch.setattr(shipments_api, "_store", store)
    monkeypatch.setattr(shipments_api, "_packs", packs)
    monkeypatch.setattr(shipments_api, "_config", config)
    app = FastAPI()
    app.state.bus = FakeBus()
    app.include_router(shipments_api.router)
    client = TestClient(app)
    return types.SimpleNamespace(client=client, store=store, bus=app.state.bus)


def _add_ups_shipment(env):
    resp = env.client.post(
        "/api/shipments",
        json={"tracking_number": UPS_NUMBER, "carrier": "ups"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["shipment"]


# -- list / summary ----------------------------------------------------------


def test_list_empty(env):
    resp = env.client.get("/api/shipments")
    assert resp.status_code == 200
    body = resp.json()
    assert body["shipments"] == []
    assert body["summary"] == {"active": 0, "arriving_today": 0, "exceptions": 0}


def test_list_and_filters(env):
    shipment = _add_ups_shipment(env)
    resp = env.client.get("/api/shipments")
    body = resp.json()
    assert len(body["shipments"]) == 1
    assert body["summary"]["active"] == 1

    assert len(env.client.get("/api/shipments?status=active").json()["shipments"]) == 1
    assert env.client.get("/api/shipments?status=archived").json()["shipments"] == []
    assert len(env.client.get("/api/shipments?q=1Z999").json()["shipments"]) == 1
    assert env.client.get("/api/shipments?q=nomatch").json()["shipments"] == []
    assert shipment["tracking_number"] == UPS_NUMBER


# -- detail -------------------------------------------------------------------


def test_detail(env):
    shipment = _add_ups_shipment(env)
    resp = env.client.get("/api/shipments/%s" % shipment["id"])
    assert resp.status_code == 200
    body = resp.json()
    assert body["shipment"]["id"] == shipment["id"]
    assert body["events"] == []
    assert body["numbers"][0]["number"] == UPS_NUMBER
    assert body["numbers"][0]["role"] == "primary"
    assert body["order"] is None


def test_detail_404(env):
    assert env.client.get("/api/shipments/shp_nope").status_code == 404


# -- manual add ---------------------------------------------------------------


def test_manual_add_with_carrier(env):
    shipment = _add_ups_shipment(env)
    assert shipment["carrier"] == "ups"
    assert shipment["status"] == "active"
    assert shipment["source"] == "manual"
    assert "shipment.updated" in env.bus.types()


def test_manual_add_infers_carrier(env):
    resp = env.client.post("/api/shipments", json={"tracking_number": UPS_NUMBER})
    assert resp.status_code == 201, resp.text
    assert resp.json()["shipment"]["carrier"] == "ups"


def test_manual_add_ambiguous_carrier(env, monkeypatch):
    fake = types.SimpleNamespace(patterns=[r"1Z[0-9A-Z]{16}"], check_digit=None)
    packs = dict(shipments_api._packs)
    packs["fakecarrier"] = fake
    monkeypatch.setattr(shipments_api, "_packs", packs)
    resp = env.client.post("/api/shipments", json={"tracking_number": UPS_NUMBER})
    assert resp.status_code == 202, resp.text
    candidate = resp.json()["candidate"]
    assert set(candidate["candidate_carriers"]) == {"ups", "fakecarrier"}
    assert "shipment.candidate" in env.bus.types()


def test_manual_add_validation_failures(env):
    resp = env.client.post(
        "/api/shipments", json={"tracking_number": "garbage", "carrier": "ups"}
    )
    assert resp.status_code == 400
    resp = env.client.post(
        "/api/shipments", json={"tracking_number": UPS_NUMBER, "carrier": "nope"}
    )
    assert resp.status_code == 400
    resp = env.client.post("/api/shipments", json={})
    assert resp.status_code == 400


def test_manual_add_paste_text_auto_add(env):
    resp = env.client.post(
        "/api/shipments",
        json={"text": "track it: https://www.ups.com/track?tracknum=%s" % UPS_NUMBER},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["shipment"]["tracking_number"] == UPS_NUMBER


def test_manual_add_paste_text_queues_low_confidence(env):
    resp = env.client.post(
        "/api/shipments",
        json={"text": "your package %s is on the way" % UPS_NUMBER},
    )
    assert resp.status_code == 202, resp.text
    candidate = resp.json()["candidate"]
    assert candidate["tracking_number"] == UPS_NUMBER
    pending = env.client.get("/api/shipments/candidates").json()["candidates"]
    assert [c["id"] for c in pending] == [candidate["id"]]


def test_manual_add_paste_text_no_match(env):
    resp = env.client.post("/api/shipments", json={"text": "nothing to track here"})
    assert resp.status_code == 404


# -- patch --------------------------------------------------------------------


def test_patch_shipment(env):
    shipment = _add_ups_shipment(env)
    resp = env.client.patch(
        "/api/shipments/%s" % shipment["id"],
        json={"label": "Shoes", "category": "clothing"},
    )
    assert resp.status_code == 200
    body = resp.json()["shipment"]
    assert body["label"] == "Shoes"
    assert body["category"] == "clothing"

    resp = env.client.patch(
        "/api/shipments/%s" % shipment["id"], json={"status": "archived"}
    )
    assert resp.status_code == 200
    assert resp.json()["shipment"]["status"] == "archived"


def test_patch_rejections(env):
    shipment = _add_ups_shipment(env)
    assert env.client.patch(
        "/api/shipments/%s" % shipment["id"], json={"status": "bogus"}
    ).status_code == 400
    assert env.client.patch(
        "/api/shipments/%s" % shipment["id"], json={"carrier": "fedex"}
    ).status_code == 400
    assert env.client.patch(
        "/api/shipments/shp_nope", json={"label": "x"}
    ).status_code == 404


# -- candidates ---------------------------------------------------------------


def _enqueue_candidate(store, sender="orders@acme-shop.example.com"):
    return store.enqueue_candidate(
        {
            "tracking_number": UPS_NUMBER,
            "carrier": "ups",
            "confidence": 0.6,
            "layer": "body",
            "source": {"channel": "email", "sender": sender},
            "sources": [{"channel": "email", "sender": sender}],
            "context": {"sender_domain": "acme-shop.example.com"},
        },
        layer="body",
        confidence=0.6,
    )


def test_candidate_confirm_creates_shipment_and_prior(env):
    candidate_id = _enqueue_candidate(env.store)
    resp = env.client.post("/api/shipments/candidates/%s/confirm" % candidate_id)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "confirmed"
    assert body["shipment"]["tracking_number"] == UPS_NUMBER
    # The UI re-fetches on shipment.updated.
    assert "shipment.updated" in env.bus.types()

    prior = env.store.get_prior("domain", "acme-shop.example.com", "ups")
    assert prior["hits"] == 1
    assert prior["misses"] == 0

    # Queue drained; double-confirm is a 404 (no longer pending).
    assert env.client.get("/api/shipments/candidates").json()["candidates"] == []
    assert env.client.post(
        "/api/shipments/candidates/%s/confirm" % candidate_id
    ).status_code == 404


def test_candidate_reject_records_miss(env):
    candidate_id = _enqueue_candidate(env.store)
    resp = env.client.post("/api/shipments/candidates/%s/reject" % candidate_id)
    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"

    prior = env.store.get_prior("domain", "acme-shop.example.com", "ups")
    assert prior["hits"] == 0
    assert prior["misses"] == 1
    # Rejection never materializes a shipment.
    assert env.client.get("/api/shipments").json()["shipments"] == []


def test_candidate_unknown_404(env):
    assert env.client.post("/api/shipments/candidates/cand_nope/confirm").status_code == 404
    assert env.client.post("/api/shipments/candidates/cand_nope/reject").status_code == 404


# -- domain rules -------------------------------------------------------------


def test_domain_rules(env):
    resp = env.client.post(
        "/api/shipments/domain-rules",
        json={"domain": "Acme-Shop.com", "category": "shopping", "display_name": "Acme"},
    )
    assert resp.status_code == 201, resp.text
    rule = resp.json()["rule"]
    assert rule["domain"] == "acme-shop.com"
    assert rule["category"] == "shopping"

    rules = env.client.get("/api/shipments/domain-rules").json()["rules"]
    assert [r["domain"] for r in rules] == ["acme-shop.com"]

    assert env.client.post(
        "/api/shipments/domain-rules", json={"domain": "not a domain"}
    ).status_code == 400


# -- eval labeling ------------------------------------------------------------


def test_eval_labeling(env):
    eval_id = env.store.record_eval_label(
        {"tracking_number": UPS_NUMBER, "carrier": "ups"}, layer="body",
        predicted="queue",
    )
    rows = env.client.get("/api/shipments/eval").json()["candidates"]
    assert [r["id"] for r in rows] == [eval_id]
    assert rows[0]["candidate"]["tracking_number"] == UPS_NUMBER

    resp = env.client.post(
        "/api/shipments/eval/%d/label" % eval_id, json={"label": "correct"}
    )
    assert resp.status_code == 200
    # Labeled rows leave the unlabeled queue.
    assert env.client.get("/api/shipments/eval").json()["candidates"] == []

    assert env.client.post(
        "/api/shipments/eval/%d/label" % eval_id, json={"label": "bogus"}
    ).status_code == 400
    assert env.client.post(
        "/api/shipments/eval/9999/label", json={"label": "correct"}
    ).status_code == 404


# -- UPS webhook --------------------------------------------------------------


def _ups_payload(**overrides):
    payload = {
        "trackingNumber": UPS_NUMBER,
        "events": [
            {
                "statusCode": "DL",
                "statusDescription": "Delivered",
                "date": "20260724",
                "time": "143000",
                "city": "Mississauga",
            }
        ],
    }
    payload.update(overrides)
    return payload


def test_ups_webhook_happy_path(env):
    shipment = _add_ups_shipment(env)
    secret = env.client.post(
        "/api/shipments/webhook/ups/rotate-secret"
    ).json()["path"].rsplit("/", 1)[1]

    resp = env.client.post(
        "/api/shipments/webhook/ups/%s" % secret, json=_ups_payload()
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"status": "ok", "events": 1}

    detail = env.client.get("/api/shipments/%s" % shipment["id"]).json()
    assert detail["shipment"]["milestone"] == "delivered"
    assert detail["shipment"]["status"] == "delivered"
    assert len(detail["events"]) == 1
    event = detail["events"][0]
    assert event["milestone"] == "delivered"
    assert event["timestamp"] == "2026-07-24T14:30:00"
    assert event["location"] == "Mississauga"

    bus_types = env.bus.types()
    assert "shipment.updated" in bus_types
    assert "shipment.milestone" in bus_types


def test_ups_webhook_wrong_secret_404(env):
    resp = env.client.post(
        "/api/shipments/webhook/ups/%s" % ("0" * 32), json=_ups_payload()
    )
    assert resp.status_code == 404


def test_ups_webhook_bad_payload_400(env):
    secret = env.client.post(
        "/api/shipments/webhook/ups/rotate-secret"
    ).json()["path"].rsplit("/", 1)[1]
    url = "/api/shipments/webhook/ups/%s" % secret

    # Unknown top-level field rejected (strict allowlist).
    resp = env.client.post(url, json=_ups_payload(evil="<script>"))
    assert resp.status_code == 400
    # Unknown event field rejected.
    resp = env.client.post(
        url, json=_ups_payload(events=[{"statusCode": "DL", "surprise": "x"}])
    )
    assert resp.status_code == 400
    # Wrong types rejected.
    resp = env.client.post(url, json={"trackingNumber": 123})
    assert resp.status_code == 400
    resp = env.client.post(url, json=_ups_payload(events="not-a-list"))
    assert resp.status_code == 400
    # Non-UPS number rejected before any store/logging path.
    resp = env.client.post(url, json=_ups_payload(trackingNumber="'; DROP TABLE--"))
    assert resp.status_code == 400


def test_ups_webhook_unknown_number_ignored(env):
    secret = env.client.post(
        "/api/shipments/webhook/ups/rotate-secret"
    ).json()["path"].rsplit("/", 1)[1]
    resp = env.client.post(
        "/api/shipments/webhook/ups/%s" % secret, json=_ups_payload()
    )
    assert resp.status_code == 202
    assert resp.json()["reason"] == "unknown_number"


def test_ups_webhook_secret_persisted_and_rotates(env):
    # First webhook call lazily generates and persists the secret.
    first = env.store.get_state("ups_webhook_secret")
    assert first is None
    env.client.post("/api/shipments/webhook/ups/%s" % ("0" * 32), json={})
    generated = env.store.get_state("ups_webhook_secret")
    assert generated and len(generated) == 32

    resp = env.client.post("/api/shipments/webhook/ups/rotate-secret")
    assert resp.status_code == 200
    new_path = resp.json()["path"]
    new_secret = new_path.rsplit("/", 1)[1]
    assert new_secret != generated
    assert env.store.get_state("ups_webhook_secret") == new_secret
    # Old secret now 404s.
    assert env.client.post(
        "/api/shipments/webhook/ups/%s" % generated, json=_ups_payload()
    ).status_code == 404
