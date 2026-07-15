"""
weekly-digest empty-sessions early return (aos health report, 2026-07-15).

When there are no session exports, scan_sessions() takes an early return that
must still carry every key the report consumers read — otherwise the digest
KeyErrors on `total_duration_min` / `avg_duration_min` and fails silently every
week (12 straight failures since 2026-06-28). This test pins the early return's
key set to the normal return's, and exercises the consumer render.
"""

import datetime
import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
DIGEST = REPO_ROOT / "core" / "bin" / "crons" / "weekly-digest"


def _load():
    loader = SourceFileLoader("weekly_digest_under_test", str(DIGEST))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


# Every key a report consumer reads off the sessions dict (lines ~493/533).
CONSUMER_KEYS = {
    "count",
    "topics",
    "projects",
    "tools",
    "total_messages",
    "total_duration_min",
    "avg_duration_min",
}


def test_empty_sessions_return_has_all_consumer_keys(tmp_path):
    m = _load()
    m.SESSIONS_DIR = tmp_path / "does-not-exist"
    result = m.scan_sessions(datetime.date(2026, 1, 1), datetime.date(2026, 1, 7))
    missing = CONSUMER_KEYS - set(result)
    assert not missing, f"empty scan_sessions() drops keys: {missing}"
    assert result["total_duration_min"] == 0
    assert result["avg_duration_min"] == 0


def test_duration_consumer_renders_without_keyerror(tmp_path):
    m = _load()
    m.SESSIONS_DIR = tmp_path / "does-not-exist"
    s = m.scan_sessions(datetime.date(2026, 1, 1), datetime.date(2026, 1, 7))
    # The two f-strings that used to KeyError every week.
    assert f"{s['total_duration_min']}min (~{s['avg_duration_min']}min avg)" == "0min (~0min avg)"
    assert f"Sessions: {s['count']} ({s['total_duration_min']}min)" == "Sessions: 0 (0min)"
