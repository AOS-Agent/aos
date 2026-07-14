"""Apple Photos signal adapter.

Extracts physical-presence signals from the local Apple Photos library at:
  ~/Pictures/Photos Library.photoslibrary/database/Photos.sqlite

The Photos.sqlite file is a Core Data store with face-detection metadata:
  ZPERSON         — each recognized person (name, face count, verification)
  ZDETECTEDFACE   — each face detection, linking a person to an asset
  ZASSET          — each photo/video (date, location, camera source)

The adapter:
  * Copies Photos.sqlite to a temp directory before reading (avoids lock).
  * Matches person_index entries to ZPERSON rows by full name / first name.
  * Builds a PhysicalPresenceSignal per matched person covering:
      - total photos, verified flag, first/last dates, temporal buckets
      - location clusters (~1km, count >= 2)
      - co-occurrence with other persons (top 20, guarded by face count)
      - camera source breakdown (operator-taken vs received via messaging)
      - Photos ML age/gender hints (pass-through)

Core Data timestamps: seconds since 2001-01-01. Converted via
_core_data_to_iso() to ISO 8601 UTC.
"""
from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import statistics
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import ClassVar

from ..types import PersonSignals, PhysicalPresenceSignal, SignalType
from .base import SignalAdapter

log = logging.getLogger(__name__)

# Core Data epoch offset: seconds from 1970-01-01 to 2001-01-01.
CORE_DATA_EPOCH_OFFSET = 978307200

DEFAULT_DB_PATH = (
    Path.home()
    / "Pictures"
    / "Photos Library.photoslibrary"
    / "database"
    / "Photos.sqlite"
)

# Bundle identifiers that indicate a photo was received via a messaging
# app (vs taken with the operator's own camera).
MESSAGING_BUNDLE_IDS: frozenset[str] = frozenset(
    {
        "net.whatsapp.WhatsApp",
        "com.apple.MobileSMS",
        "com.apple.Messages",
        "com.apple.icloud.fmfd",
        "net.telegram.messenger",
    }
)

OPERATOR_CAMERA_BUNDLE_ID = "com.apple.camera"

# Minimum face count before the adapter will compute co-occurrence for a
# person. Co-occurrence is a quadratic self-join — skip tiny tails.
CO_OCCURRENCE_FACE_MIN = 10

# Upper bound on rows the co-occurrence self-join will consume per
# person before the progress handler aborts it. Roughly calibrated to
# ~10s of work on a warm cache.
CO_OCCURRENCE_ROW_BUDGET = 500_000

# Top-N co-occurrence peers kept per person.
CO_OCCURRENCE_LIMIT = 20

# Location clustering precision: 0.01 degrees ≈ ~1km at the equator.
LOCATION_CLUSTER_PRECISION = 2
LOCATION_MIN_COUNT = 2


def _core_data_to_unix(z_date: float) -> float:
    """Convert Core Data timestamp (sec since 2001-01-01) to unix seconds."""
    return float(z_date) + CORE_DATA_EPOCH_OFFSET


def _core_data_to_iso(z_date: float) -> str:
    """Convert Core Data timestamp to ISO 8601 UTC string."""
    return datetime.fromtimestamp(
        _core_data_to_unix(z_date), tz=timezone.utc
    ).isoformat()


def _compute_temporal_pattern(buckets: dict[str, int]) -> str:
    """Classify temporal_pattern from YYYY-MM buckets.

    Matches the heuristic used by the other adapters so signal consumers
    see consistent vocabulary across channels. Photos use a threshold of
    5 per bucket (same as apple_messages) to mark a "consistent" run.
    """
    if not buckets:
        return "none"
    if len(buckets) == 1:
        return "one_shot"

    sorted_keys = sorted(buckets.keys())

    def _month_plus(k: str, n: int) -> str:
        y, m = k.split("-")
        y_i, m_i = int(y), int(m) + n
        while m_i > 12:
            y_i += 1
            m_i -= 12
        while m_i < 1:
            y_i -= 1
            m_i += 12
        return f"{y_i:04d}-{m_i:02d}"

    consecutive_run = 0
    for i, k in enumerate(sorted_keys):
        if buckets[k] >= 5:
            if i == 0 or sorted_keys[i - 1] == _month_plus(k, -1):
                consecutive_run += 1
                if consecutive_run >= 3:
                    break
            else:
                consecutive_run = 1
        else:
            consecutive_run = 0

    consistent = consecutive_run >= 3

    first3 = sorted_keys[:3]
    last3 = sorted_keys[-3:]
    first_avg = statistics.mean(buckets[k] for k in first3) if first3 else 0
    last_avg = statistics.mean(buckets[k] for k in last3) if last3 else 0

    if consistent:
        return "consistent"
    if first_avg > 0 and last_avg >= first_avg * 2:
        return "growing"
    if last_avg > 0 and first_avg >= last_avg * 2:
        return "fading"

    if len(sorted_keys) >= 2:
        gaps = 0
        for a, b in zip(sorted_keys, sorted_keys[1:]):
            if b != _month_plus(a, 1):
                gaps += 1
        if gaps > 0:
            return "episodic"

    return "clustered"


class _BudgetExceeded(Exception):
    """Raised from the SQLite progress handler when the row budget is spent."""


class ApplePhotosAdapter(SignalAdapter):
    """Signal adapter for macOS Apple Photos (face + location metadata)."""

    name: ClassVar[str] = "apple_photos"
    display_name: ClassVar[str] = "Apple Photos"
    platform: ClassVar[str] = "macos"
    signal_types: ClassVar[list[SignalType]] = [SignalType.PHYSICAL_PRESENCE]
    description: ClassVar[str] = (
        "Face co-occurrence, locations, camera source via Photos.sqlite"
    )
    requires: ClassVar[list[str]] = [
        "file:~/Pictures/Photos Library.photoslibrary/database/Photos.sqlite"
    ]

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH

    # ── availability ──────────────────────────────────────────────────

    def is_available(self) -> bool:
        try:
            return self.db_path.exists() and os.access(self.db_path, os.R_OK)
        except Exception:
            return False

    # ── extraction ────────────────────────────────────────────────────

    def extract_all(self, person_index: dict[str, dict]) -> dict[str, PersonSignals]:
        if not self.is_available():
            return {}
        try:
            return self._extract_all_inner(person_index)
        except Exception as e:  # pragma: no cover - defensive
            log.exception("apple_photos extract_all failed: %s", e)
            return {}

    def _extract_all_inner(
        self, person_index: dict[str, dict]
    ) -> dict[str, PersonSignals]:
        with tempfile.TemporaryDirectory(prefix="aos-intel-photos-") as tmpdir:
            tmp_db = Path(tmpdir) / "Photos.sqlite"
            try:
                shutil.copy2(self.db_path, tmp_db)
            except Exception as e:
                log.warning("apple_photos: could not copy Photos.sqlite: %s", e)
                return {}

            try:
                conn = sqlite3.connect(f"file:{tmp_db}?mode=ro", uri=True)
            except sqlite3.Error as e:
                log.warning("apple_photos: could not open Photos.sqlite: %s", e)
                return {}

            try:
                conn.row_factory = sqlite3.Row
                return self._scan_and_group(conn, person_index)
            finally:
                conn.close()

    # ── matching ──────────────────────────────────────────────────────

    def _match_persons(
        self,
        conn: sqlite3.Connection,
        person_index: dict[str, dict],
    ) -> dict[str, int]:
        """Resolve person_id → ZPERSON.Z_PK for every matched person.

        Uses the universal NameIndex (checks canonical_name, display_name,
        nickname, aliases, and phonetic variants like idris↔idrees) to
        match ZPERSON.ZFULLNAME and ZDISPLAYNAME to people.db rows.

        Only persons with ZFACECOUNT >= 1 are considered.
        """
        # Build a NameIndex from the person_index dict (controlled by caller
        # and tests). Supplement with aliases from people.db when available.
        try:
            from core.engine.people.name_matcher import NameIndex

            name_idx = NameIndex()
            for pid, info in person_index.items():
                name = info.get("name", "")
                first = name.split()[0] if name.split() else ""
                name_idx.add_person(
                    pid=pid,
                    canonical_name=name,
                    display_name=None,
                    nickname=None,
                    first_name=first,
                    last_name=None,
                    aliases=None,
                )

            # Enrich with aliases from people.db (best-effort)
            import sqlite3 as _sql
            people_db = Path.home() / ".aos" / "data" / "people.db"
            if people_db.exists():
                pconn = _sql.connect(str(people_db))
                pconn.row_factory = _sql.Row
                try:
                    for r in pconn.execute("SELECT alias, person_id FROM aliases"):
                        if r["person_id"] in person_index:
                            name_idx._add(r["alias"].lower().strip(), r["person_id"])
                    # Also add display_name and nickname from people table
                    for r in pconn.execute(
                        "SELECT id, display_name, nickname FROM people WHERE is_archived=0"
                    ):
                        if r["id"] in person_index:
                            if r["display_name"]:
                                name_idx._add(r["display_name"].lower().strip(), r["id"])
                            if r["nickname"]:
                                name_idx._add(r["nickname"].lower().strip(), r["id"])
                finally:
                    pconn.close()
        except Exception:
            name_idx = None

        try:
            rows = conn.execute(
                """
                SELECT Z_PK, ZDISPLAYNAME, ZFULLNAME, ZFACECOUNT
                FROM ZPERSON
                WHERE ZFACECOUNT >= 1
                """
            ).fetchall()
        except sqlite3.Error as e:
            log.warning("apple_photos: ZPERSON query failed: %s", e)
            return {}

        # Build ZPERSON pk → (fullname, displayname) map for reverse lookup
        zperson_names: dict[int, tuple[str, str]] = {}
        for r in rows:
            pk = int(r["Z_PK"])
            full = (r["ZFULLNAME"] or "").strip()
            disp = (r["ZDISPLAYNAME"] or "").strip()
            zperson_names[pk] = (full, disp)

        resolved: dict[str, int] = {}

        if name_idx is not None:
            # Strategy: for each ZPERSON, use the universal matcher to find
            # which person_id it corresponds to. Then for each person in
            # person_index, check if we found their ZPERSON pk.
            zpk_to_pid: dict[int, str] = {}
            for zpk, (full, disp) in zperson_names.items():
                pid = name_idx.match_any(full, disp)
                if pid and pid in person_index:
                    zpk_to_pid[zpk] = pid

            for zpk, pid in zpk_to_pid.items():
                if pid not in resolved:
                    resolved[pid] = zpk
        else:
            # Fallback: legacy exact-match if name_matcher unavailable
            by_fullname: dict[str, list[int]] = {}
            by_displayname: dict[str, list[int]] = {}
            for pk, (full, disp) in zperson_names.items():
                if full:
                    by_fullname.setdefault(full.lower(), []).append(pk)
                if disp:
                    by_displayname.setdefault(disp.lower(), []).append(pk)
            for pid, info in person_index.items():
                name = (info.get("name") or "").strip()
                if not name:
                    continue
                lower = name.lower()
                first = lower.split()[0] if lower.split() else ""
                full_hits = by_fullname.get(lower) or []
                if len(full_hits) == 1:
                    resolved[pid] = full_hits[0]
                    continue
                if first:
                    disp_hits = by_displayname.get(first) or []
                    if len(disp_hits) == 1:
                        resolved[pid] = disp_hits[0]

        return resolved

    # ── main scan ─────────────────────────────────────────────────────

    def _scan_and_group(
        self,
        conn: sqlite3.Connection,
        person_index: dict[str, dict],
    ) -> dict[str, PersonSignals]:
        resolved = self._match_persons(conn, person_index)
        if not resolved:
            return {}

        # Fetch per-person ZPERSON metadata once for verified/age/gender.
        person_meta: dict[int, sqlite3.Row] = {}
        try:
            placeholders = ",".join("?" * len(resolved))
            rows = conn.execute(
                f"""
                SELECT Z_PK, ZFACECOUNT, ZVERIFIEDTYPE, ZAGETYPE, ZGENDERTYPE,
                       ZDISPLAYNAME, ZFULLNAME
                FROM ZPERSON
                WHERE Z_PK IN ({placeholders})
                """,
                list(resolved.values()),
            ).fetchall()
            for r in rows:
                person_meta[int(r["Z_PK"])] = r
        except sqlite3.Error as e:
            log.warning("apple_photos: ZPERSON metadata query failed: %s", e)
            return {}

        # Detect the operator ZPERSON: highest ZFACECOUNT in the library.
        operator_pk = self._detect_operator_pk(conn)
        operator_home_cluster = None
        if operator_pk is not None:
            operator_home_cluster = self._dominant_location_cluster(
                conn, operator_pk
            )

        # Build an inverse map so co-occurrence results can be turned
        # back into display names without an extra query.
        pk_to_display: dict[int, str] = {}
        try:
            rows = conn.execute(
                "SELECT Z_PK, ZDISPLAYNAME, ZFULLNAME FROM ZPERSON"
            ).fetchall()
            for r in rows:
                pk_to_display[int(r["Z_PK"])] = (
                    (r["ZFULLNAME"] or r["ZDISPLAYNAME"] or "").strip()
                )
        except sqlite3.Error:
            pass

        results: dict[str, PersonSignals] = {}
        now_iso = datetime.now(tz=timezone.utc).isoformat()

        for pid, pk in resolved.items():
            try:
                signal = self._build_presence_signal(
                    conn,
                    pk,
                    person_meta.get(pk),
                    operator_home_cluster,
                    pk_to_display,
                )
            except Exception as e:  # pragma: no cover - defensive
                log.warning("apple_photos: per-person build failed pk=%s: %s", pk, e)
                continue

            if signal.total_photos == 0:
                continue

            info = person_index.get(pid) or {}
            ps = PersonSignals(
                person_id=pid,
                person_name=info.get("name", ""),
                extracted_at=now_iso,
                source_coverage=[self.name],
            )
            ps.physical_presence.append(signal)
            results[pid] = ps

        return results

    # ── operator detection ───────────────────────────────────────────

    def _detect_operator_pk(self, conn: sqlite3.Connection) -> int | None:
        try:
            row = conn.execute(
                """
                SELECT Z_PK
                FROM ZPERSON
                WHERE ZFACECOUNT IS NOT NULL
                ORDER BY ZFACECOUNT DESC
                LIMIT 1
                """
            ).fetchone()
        except sqlite3.Error:
            return None
        return int(row["Z_PK"]) if row else None

    def _dominant_location_cluster(
        self, conn: sqlite3.Connection, pk: int
    ) -> tuple[float, float] | None:
        """Most common location cluster for person pk, or None if unknown."""
        try:
            rows = conn.execute(
                """
                SELECT a.ZLATITUDE AS lat, a.ZLONGITUDE AS lon
                FROM ZDETECTEDFACE f
                JOIN ZASSET a ON a.Z_PK = f.ZASSETFORFACE
                WHERE f.ZPERSONFORFACE = ?
                  AND a.ZLATITUDE IS NOT NULL
                  AND a.ZLONGITUDE IS NOT NULL
                """,
                (pk,),
            ).fetchall()
        except sqlite3.Error:
            return None

        if not rows:
            return None
        clusters: dict[tuple[float, float], int] = {}
        for r in rows:
            try:
                key = (
                    round(float(r["lat"]), LOCATION_CLUSTER_PRECISION),
                    round(float(r["lon"]), LOCATION_CLUSTER_PRECISION),
                )
            except (TypeError, ValueError):
                continue
            clusters[key] = clusters.get(key, 0) + 1
        if not clusters:
            return None
        return max(clusters.items(), key=lambda kv: kv[1])[0]

    # ── signal construction ──────────────────────────────────────────

    def _build_presence_signal(
        self,
        conn: sqlite3.Connection,
        pk: int,
        meta: sqlite3.Row | None,
        operator_home_cluster: tuple[float, float] | None,
        pk_to_display: dict[int, str],
    ) -> PhysicalPresenceSignal:
        sig = PhysicalPresenceSignal(source=self.name)

        if meta is not None:
            sig.verified = int(meta["ZVERIFIEDTYPE"] or 0) == 1
            age = meta["ZAGETYPE"]
            gender = meta["ZGENDERTYPE"]
            sig.detected_age_type = int(age) if age is not None else None
            sig.detected_gender = int(gender) if gender is not None else None

        # Fetch all photos this person appears in.
        #
        # ZIMPORTEDBYBUNDLEIDENTIFIER lives in ZADDITIONALASSETATTRIBUTES
        # on modern macOS, not ZASSET — join via the ZASSET foreign key.
        # We LEFT JOIN so assets without an additional-attributes row still
        # count, just with a NULL bundle_id.
        try:
            rows = conn.execute(
                """
                SELECT a.Z_PK AS asset_pk,
                       a.ZDATECREATED AS z_date,
                       a.ZLATITUDE AS lat,
                       a.ZLONGITUDE AS lon,
                       aa.ZIMPORTEDBYBUNDLEIDENTIFIER AS bundle_id
                FROM ZDETECTEDFACE f
                JOIN ZASSET a ON a.Z_PK = f.ZASSETFORFACE
                LEFT JOIN ZADDITIONALASSETATTRIBUTES aa ON aa.ZASSET = a.Z_PK
                WHERE f.ZPERSONFORFACE = ?
                """,
                (pk,),
            ).fetchall()
        except sqlite3.Error:
            # Fallback: some macOS versions keep the bundle id on ZASSET itself.
            try:
                rows = conn.execute(
                    """
                    SELECT a.Z_PK AS asset_pk,
                           a.ZDATECREATED AS z_date,
                           a.ZLATITUDE AS lat,
                           a.ZLONGITUDE AS lon,
                           a.ZIMPORTEDBYBUNDLEIDENTIFIER AS bundle_id
                    FROM ZDETECTEDFACE f
                    JOIN ZASSET a ON a.Z_PK = f.ZASSETFORFACE
                    WHERE f.ZPERSONFORFACE = ?
                    """,
                    (pk,),
                ).fetchall()
            except sqlite3.Error as e2:
                log.warning("apple_photos: asset query failed pk=%s: %s", pk, e2)
                return sig

        if not rows:
            return sig

        sig.total_photos = len(rows)

        first_date: float | None = None
        last_date: float | None = None
        buckets: dict[str, int] = {}
        clusters: dict[tuple[float, float], int] = {}
        home_count = 0
        operator_taken = 0
        received = 0

        for r in rows:
            # Date + buckets.
            try:
                z_date = float(r["z_date"] or 0)
            except (TypeError, ValueError):
                z_date = 0.0
            if z_date:
                if first_date is None or z_date < first_date:
                    first_date = z_date
                if last_date is None or z_date > last_date:
                    last_date = z_date
                try:
                    dt = datetime.fromtimestamp(
                        _core_data_to_unix(z_date), tz=timezone.utc
                    )
                    bucket_key = f"{dt.year:04d}-{dt.month:02d}"
                    buckets[bucket_key] = buckets.get(bucket_key, 0) + 1
                except (ValueError, OSError, OverflowError):
                    pass

            # Location clustering.
            lat = r["lat"]
            lon = r["lon"]
            if lat is not None and lon is not None:
                try:
                    key = (
                        round(float(lat), LOCATION_CLUSTER_PRECISION),
                        round(float(lon), LOCATION_CLUSTER_PRECISION),
                    )
                    clusters[key] = clusters.get(key, 0) + 1
                    if operator_home_cluster and key == operator_home_cluster:
                        home_count += 1
                except (TypeError, ValueError):
                    pass

            # Camera source split.
            bundle = (r["bundle_id"] or "").strip() if r["bundle_id"] else ""
            if bundle == OPERATOR_CAMERA_BUNDLE_ID:
                operator_taken += 1
            elif bundle in MESSAGING_BUNDLE_IDS:
                received += 1

        if first_date is not None:
            sig.first_photo_date = _core_data_to_iso(first_date)
        if last_date is not None:
            sig.last_photo_date = _core_data_to_iso(last_date)
        sig.temporal_buckets = buckets
        sig.temporal_pattern = _compute_temporal_pattern(buckets)

        sig.locations = [
            {"lat": lat, "lon": lon, "count": count}
            for (lat, lon), count in sorted(
                clusters.items(), key=lambda kv: kv[1], reverse=True
            )
            if count >= LOCATION_MIN_COUNT
        ]
        sig.home_location_photos = home_count

        total = sig.total_photos
        sig.operator_taken_pct = (operator_taken / total) if total else 0.0
        sig.received_pct = (received / total) if total else 0.0

        # Co-occurrence — only if face count is above threshold.
        face_count = 0
        if meta is not None:
            try:
                face_count = int(meta["ZFACECOUNT"] or 0)
            except (TypeError, ValueError):
                face_count = 0
        if face_count >= CO_OCCURRENCE_FACE_MIN:
            sig.co_photographed_with = self._co_occurrence(
                conn, pk, pk_to_display
            )

        return sig

    def _co_occurrence(
        self,
        conn: sqlite3.Connection,
        pk: int,
        pk_to_display: dict[int, str],
    ) -> list[dict]:
        """Top-N co-photographed persons via a bounded self-join."""
        state = {"rows": 0}

        def _budget_check() -> int:
            state["rows"] += 100
            if state["rows"] > CO_OCCURRENCE_ROW_BUDGET:
                # Returning non-zero aborts the current SQLite operation.
                return 1
            return 0

        conn.set_progress_handler(_budget_check, 100)
        try:
            try:
                rows = conn.execute(
                    """
                    SELECT other_person.Z_PK AS other_pk,
                           other_person.ZDISPLAYNAME AS other_name,
                           COUNT(*) AS shared
                    FROM ZDETECTEDFACE df1
                    JOIN ZDETECTEDFACE df2
                      ON df1.ZASSETFORFACE = df2.ZASSETFORFACE
                     AND df1.Z_PK != df2.Z_PK
                    JOIN ZPERSON other_person
                      ON df2.ZPERSONFORFACE = other_person.Z_PK
                    WHERE df1.ZPERSONFORFACE = ?
                      AND other_person.Z_PK != ?
                    GROUP BY other_person.Z_PK
                    ORDER BY shared DESC
                    LIMIT ?
                    """,
                    (pk, pk, CO_OCCURRENCE_LIMIT),
                ).fetchall()
            except sqlite3.OperationalError as e:
                # Progress-handler abort surfaces as OperationalError.
                log.info(
                    "apple_photos: co-occurrence budget exceeded pk=%s: %s", pk, e
                )
                return []
            except sqlite3.Error as e:
                log.warning("apple_photos: co-occurrence query failed pk=%s: %s", pk, e)
                return []
        finally:
            conn.set_progress_handler(None, 0)

        results: list[dict] = []
        for r in rows:
            other_pk = int(r["other_pk"])
            display = pk_to_display.get(other_pk) or (r["other_name"] or "").strip()
            if not display:
                continue
            results.append({"name": display, "shared_photos": int(r["shared"])})
        return results
