"""Tests for the Apple Photos signal adapter.

Builds a tiny fake Photos.sqlite with the minimal Core Data schema the
adapter queries, populates it with a small hand-crafted dataset, and
verifies the produced PhysicalPresenceSignal aggregates.

Fixture overview (ZFACECOUNT chosen so Alice and Charlie exceed the
co-occurrence threshold of 10, Bob does not):

  ZPERSON
    pk=1  Alice Smith   ZFACECOUNT=12  verified  age=3
    pk=2  Bob            ZFACECOUNT=3   unverified
    pk=3  Charlie        ZFACECOUNT=15
    pk=4  Operator       ZFACECOUNT=99  (dominant — acts as "me")

  ZASSET  (6 assets + 2 operator-only assets to anchor home cluster)
    a1 — 2026-01-15  37.7749,-122.4194  com.apple.camera
    a2 — 2026-01-20  37.7749,-122.4194  com.apple.camera
    a3 — 2026-01-25  37.7749,-122.4194  net.whatsapp.WhatsApp
    a4 — 2026-02-05  40.7128,-74.0060   com.apple.camera
    a5 — 2026-02-10  40.7128,-74.0060   net.whatsapp.WhatsApp
    a6 — 2026-02-15  NULL,NULL          NULL
    a7 — 2026-01-01  37.7749,-122.4194  com.apple.camera  (operator anchor)
    a8 — 2026-01-02  37.7749,-122.4194  com.apple.camera  (operator anchor)

  ZDETECTEDFACE (person_pk, asset_pk)
    Alice: a1, a2, a3, a4, a5, a6              → 6 photos
    Bob:   a1, a4, a6                          → 3 photos
    Charlie: a1, a2, a3, a4, a5, a6, and
             two extra assets (a1,a2 duplicates are illegal, so use
             6 shared + 2 charlie-only). Target 8 photos total, with
             Alice+Charlie co-occurring on a1,a2,a3 (3 shared).

  For the "3 shared" co-occurrence target: place Alice in
  {a1..a6} and Charlie in {a1,a2,a3, a7b,a8b,...} — but Charlie must
  have 8 rows and share 3 with Alice. We'll give Charlie 3 shared
  (a1,a2,a3) + 5 solo (c1..c5). Total 8.

  Operator anchors on a7, a8 so its dominant cluster is (37.77,-122.42)
  — and Alice has 3 photos at that cluster (a1,a2,a3) giving a
  non-zero home_location_photos count.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.engine.people.intel.sources.apple_photos import (
    CORE_DATA_EPOCH_OFFSET,
    ApplePhotosAdapter,
)
from core.engine.people.intel.types import PersonSignals, SignalType


# ── fixture helpers ───────────────────────────────────────────────────


def _iso_to_core_data(iso: str) -> float:
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp() - CORE_DATA_EPOCH_OFFSET


def _make_fixture_db(path: Path) -> None:
    """Create a minimal Photos.sqlite fixture with canned data."""
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(
            """
            CREATE TABLE ZPERSON (
                Z_PK INTEGER PRIMARY KEY,
                ZDISPLAYNAME TEXT,
                ZFULLNAME TEXT,
                ZFACECOUNT INTEGER,
                ZVERIFIEDTYPE INTEGER,
                ZAGETYPE INTEGER,
                ZGENDERTYPE INTEGER
            );
            CREATE TABLE ZDETECTEDFACE (
                Z_PK INTEGER PRIMARY KEY AUTOINCREMENT,
                ZPERSONFORFACE INTEGER,
                ZASSETFORFACE INTEGER
            );
            CREATE TABLE ZASSET (
                Z_PK INTEGER PRIMARY KEY,
                ZDATECREATED REAL,
                ZLATITUDE REAL,
                ZLONGITUDE REAL,
                ZIMPORTEDBYBUNDLEIDENTIFIER TEXT
            );
            """
        )

        persons = [
            # (pk, display, full, facecount, verified, age, gender)
            (1, "Alice", "Alice Smith", 12, 1, 3, 2),
            (2, "Bob", None, 3, 0, None, None),
            (3, "Charlie", "Charlie Brown", 15, 0, None, None),
            (4, "Operator", "The Operator", 99, 1, None, None),
        ]
        conn.executemany(
            """
            INSERT INTO ZPERSON
              (Z_PK, ZDISPLAYNAME, ZFULLNAME, ZFACECOUNT,
               ZVERIFIEDTYPE, ZAGETYPE, ZGENDERTYPE)
            VALUES (?,?,?,?,?,?,?)
            """,
            persons,
        )

        # Assets: (pk, iso date, lat, lon, bundle)
        assets = [
            (1,  "2026-01-15T12:00:00+00:00", 37.7749, -122.4194, "com.apple.camera"),
            (2,  "2026-01-20T12:00:00+00:00", 37.7749, -122.4194, "com.apple.camera"),
            (3,  "2026-01-25T12:00:00+00:00", 37.7749, -122.4194, "net.whatsapp.WhatsApp"),
            (4,  "2026-02-05T12:00:00+00:00", 40.7128, -74.0060,  "com.apple.camera"),
            (5,  "2026-02-10T12:00:00+00:00", 40.7128, -74.0060,  "net.whatsapp.WhatsApp"),
            (6,  "2026-02-15T12:00:00+00:00", None,    None,      None),
            # Operator-only anchors at San Francisco cluster.
            (7,  "2026-01-01T12:00:00+00:00", 37.7749, -122.4194, "com.apple.camera"),
            (8,  "2026-01-02T12:00:00+00:00", 37.7749, -122.4194, "com.apple.camera"),
            # Charlie-only solo assets (5 of them to bring Charlie to 8).
            (11, "2026-02-20T12:00:00+00:00", 40.7128, -74.0060,  "com.apple.camera"),
            (12, "2026-02-21T12:00:00+00:00", 40.7128, -74.0060,  "com.apple.camera"),
            (13, "2026-02-22T12:00:00+00:00", None,    None,      None),
            (14, "2026-02-23T12:00:00+00:00", None,    None,      None),
            (15, "2026-02-24T12:00:00+00:00", None,    None,      None),
        ]
        conn.executemany(
            """
            INSERT INTO ZASSET
              (Z_PK, ZDATECREATED, ZLATITUDE, ZLONGITUDE,
               ZIMPORTEDBYBUNDLEIDENTIFIER)
            VALUES (?,?,?,?,?)
            """,
            [
                (pk, _iso_to_core_data(iso), lat, lon, bundle)
                for (pk, iso, lat, lon, bundle) in assets
            ],
        )

        # Face detections.
        alice_assets = [1, 2, 3, 4, 5, 6]           # 6 photos
        bob_assets = [1, 4, 6]                      # 3 photos
        charlie_assets = [1, 2, 3, 11, 12, 13, 14, 15]  # 8 photos
        operator_assets = [7, 8]

        face_rows: list[tuple[int, int]] = []
        for a in alice_assets:
            face_rows.append((1, a))
        for a in bob_assets:
            face_rows.append((2, a))
        for a in charlie_assets:
            face_rows.append((3, a))
        for a in operator_assets:
            face_rows.append((4, a))

        conn.executemany(
            "INSERT INTO ZDETECTEDFACE (ZPERSONFORFACE, ZASSETFORFACE) VALUES (?,?)",
            face_rows,
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture()
def fixture_db(tmp_path: Path) -> Path:
    db = tmp_path / "Photos.sqlite"
    _make_fixture_db(db)
    return db


@pytest.fixture()
def person_index() -> dict[str, dict]:
    return {
        "p_alice": {
            "name": "Alice Smith",
            "phones": [],
            "emails": [],
            "wa_jids": [],
        },
        "p_bob": {
            "name": "Bob",
            "phones": [],
            "emails": [],
            "wa_jids": [],
        },
        "p_dave": {  # unmatched — no such person in fixture
            "name": "Dave Nonexistent",
            "phones": [],
            "emails": [],
            "wa_jids": [],
        },
    }


# ── tests ─────────────────────────────────────────────────────────────


def test_is_available_true_when_db_exists(fixture_db: Path) -> None:
    adapter = ApplePhotosAdapter(db_path=fixture_db)
    assert adapter.is_available() is True


def test_is_available_false_when_missing(tmp_path: Path) -> None:
    adapter = ApplePhotosAdapter(db_path=tmp_path / "nope.sqlite")
    assert adapter.is_available() is False


def test_extract_returns_dict(
    fixture_db: Path, person_index: dict[str, dict]
) -> None:
    adapter = ApplePhotosAdapter(db_path=fixture_db)
    result = adapter.extract_all(person_index)
    assert isinstance(result, dict)
    # At least Alice and Bob should match.
    assert "p_alice" in result
    assert "p_bob" in result


def test_matches_person_by_full_name(
    fixture_db: Path, person_index: dict[str, dict]
) -> None:
    adapter = ApplePhotosAdapter(db_path=fixture_db)
    result = adapter.extract_all(person_index)
    alice = result["p_alice"]
    assert isinstance(alice, PersonSignals)
    assert alice.physical_presence
    assert alice.person_name == "Alice Smith"


def test_matches_person_by_first_name(
    fixture_db: Path, person_index: dict[str, dict]
) -> None:
    adapter = ApplePhotosAdapter(db_path=fixture_db)
    result = adapter.extract_all(person_index)
    # Bob matches on ZDISPLAYNAME only (ZFULLNAME is NULL).
    assert "p_bob" in result
    assert result["p_bob"].physical_presence[0].total_photos == 3


def test_total_photos_counted(
    fixture_db: Path, person_index: dict[str, dict]
) -> None:
    adapter = ApplePhotosAdapter(db_path=fixture_db)
    result = adapter.extract_all(person_index)
    assert result["p_alice"].physical_presence[0].total_photos == 6
    assert result["p_bob"].physical_presence[0].total_photos == 3


def test_verified_flag_propagates(
    fixture_db: Path, person_index: dict[str, dict]
) -> None:
    adapter = ApplePhotosAdapter(db_path=fixture_db)
    result = adapter.extract_all(person_index)
    assert result["p_alice"].physical_presence[0].verified is True
    assert result["p_bob"].physical_presence[0].verified is False


def test_co_photographed_with_above_threshold(
    fixture_db: Path, person_index: dict[str, dict]
) -> None:
    adapter = ApplePhotosAdapter(db_path=fixture_db)
    result = adapter.extract_all(person_index)
    # Alice has ZFACECOUNT=12 (>=10) → should get co-occurrence.
    alice_sig = result["p_alice"].physical_presence[0]
    assert alice_sig.co_photographed_with
    # Alice + Charlie share assets 1, 2, 3 — 3 shared photos.
    charlie_entry = next(
        (e for e in alice_sig.co_photographed_with if "Charlie" in e["name"]),
        None,
    )
    assert charlie_entry is not None
    assert charlie_entry["shared_photos"] == 3

    # Bob has ZFACECOUNT=3 (<10) → empty co-occurrence.
    bob_sig = result["p_bob"].physical_presence[0]
    assert bob_sig.co_photographed_with == []


def test_temporal_buckets_populated(
    fixture_db: Path, person_index: dict[str, dict]
) -> None:
    adapter = ApplePhotosAdapter(db_path=fixture_db)
    result = adapter.extract_all(person_index)
    alice_sig = result["p_alice"].physical_presence[0]
    # Alice has photos spanning Jan 2026 and Feb 2026 → 2 buckets.
    assert set(alice_sig.temporal_buckets.keys()) == {"2026-01", "2026-02"}
    assert sum(alice_sig.temporal_buckets.values()) == 6


def test_location_clusters(
    fixture_db: Path, person_index: dict[str, dict]
) -> None:
    adapter = ApplePhotosAdapter(db_path=fixture_db)
    result = adapter.extract_all(person_index)
    alice_sig = result["p_alice"].physical_presence[0]
    # Alice has 3 photos at SF cluster and 2 at NYC cluster — both
    # above the min-count threshold of 2.
    assert len(alice_sig.locations) == 2
    lat_rounded = {round(loc["lat"], 2) for loc in alice_sig.locations}
    assert 37.77 in lat_rounded
    assert 40.71 in lat_rounded


def test_camera_source_split(
    fixture_db: Path, person_index: dict[str, dict]
) -> None:
    adapter = ApplePhotosAdapter(db_path=fixture_db)
    result = adapter.extract_all(person_index)
    alice_sig = result["p_alice"].physical_presence[0]
    # Alice: 6 total, 3 camera (a1,a2,a4), 2 whatsapp (a3,a5), 1 null (a6)
    assert alice_sig.operator_taken_pct == pytest.approx(3 / 6)
    assert alice_sig.received_pct == pytest.approx(2 / 6)


def test_detected_age_and_gender_propagate(
    fixture_db: Path, person_index: dict[str, dict]
) -> None:
    adapter = ApplePhotosAdapter(db_path=fixture_db)
    result = adapter.extract_all(person_index)
    alice_sig = result["p_alice"].physical_presence[0]
    assert alice_sig.detected_age_type == 3
    assert alice_sig.detected_gender == 2
    bob_sig = result["p_bob"].physical_presence[0]
    assert bob_sig.detected_age_type is None
    assert bob_sig.detected_gender is None


def test_unmatched_person_absent(
    fixture_db: Path, person_index: dict[str, dict]
) -> None:
    adapter = ApplePhotosAdapter(db_path=fixture_db)
    result = adapter.extract_all(person_index)
    assert "p_dave" not in result


def test_extract_returns_empty_when_db_missing(
    tmp_path: Path, person_index: dict[str, dict]
) -> None:
    adapter = ApplePhotosAdapter(db_path=tmp_path / "missing.sqlite")
    assert adapter.extract_all(person_index) == {}


def test_home_location_photos_from_operator_cluster(
    fixture_db: Path, person_index: dict[str, dict]
) -> None:
    """Operator detection + home cluster should count Alice's SF photos."""
    adapter = ApplePhotosAdapter(db_path=fixture_db)
    result = adapter.extract_all(person_index)
    alice_sig = result["p_alice"].physical_presence[0]
    # Operator ZPERSON pk=4 (highest ZFACECOUNT=99). Its dominant cluster
    # is SF (a7,a8). Alice has 3 photos at SF (a1,a2,a3).
    assert alice_sig.home_location_photos == 3


def test_signal_type_is_physical_presence() -> None:
    adapter = ApplePhotosAdapter()
    assert SignalType.PHYSICAL_PRESENCE in adapter.signal_types
