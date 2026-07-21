

# ── Durability filter (operator feedback 2026-07-21: momentary logistics) ──
from datetime import datetime, timedelta, timezone

from core.engine.comms.ambient.digest import _is_durable


def _days_ago(n):
    return (datetime.now(timezone.utc) - timedelta(days=n)).isoformat()


class TestDurability:
    def test_immediate_due_fresh_survives(self):
        assert _is_durable("in 5 minutes", _days_ago(0)) is True

    def test_immediate_due_stale_dies(self):
        assert _is_durable("in 5 minutes", _days_ago(3)) is False
        assert _is_durable("right back", _days_ago(2)) is False
        assert _is_durable("tonight", _days_ago(5)) is False

    def test_undated_fresh_survives(self):
        assert _is_durable(None, _days_ago(3)) is True

    def test_undated_old_dies(self):
        assert _is_durable(None, _days_ago(30)) is False

    def test_dated_nonimmediate_rides(self):
        assert _is_durable("before Maghrib on Friday", _days_ago(40)) is True
        assert _is_durable("2026-08-20", _days_ago(60)) is True

    def test_bad_timestamp_failsafe(self):
        assert _is_durable(None, "garbage") is True
