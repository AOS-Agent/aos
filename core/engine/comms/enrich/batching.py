"""Person-day batching with tiny-day merge.

The fixed ~40K-token harness tax per `claude --print` call (sample §7) means
cost is dominated by call COUNT, not message count. So batches must be dense.
The locked design is one batch per (correspondent, day, channel); this module
adds the merge the sample §7/§8 requires — a person's thin days (< min_batch_msgs)
roll together, by ISO week, into denser batches, while genuinely busy days stand
alone. Every batch is chunked to max_batch_msgs so a single prompt stays bounded.

batch_key is deterministic (`person:channel:unit:chunk`) so the same input always
yields the same batches — the watermark and entity ids stay stable across resume.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Iterable


@dataclass
class Batch:
    batch_key: str
    person_id: str | None       # resolved correspondent (None if unresolved)
    channel: str
    messages: list[dict]

    @property
    def n(self) -> int:
        return len(self.messages)


def _correspondent_key(m: dict) -> tuple[str | None, str]:
    """Return (person_id, group_key). group_key buckets messages; person_id is
    the resolved id carried onto the entity (may be None)."""
    pid = m.get("person_id")
    if pid:
        return pid, pid
    # Unresolved: keep distinct handles apart — the counterpart handle by
    # direction. Never lump all unknowns into one bucket.
    if m.get("direction") == "outbound":
        handle = m.get("recipient_id")
    else:
        handle = m.get("sender_id")
    if handle:
        return None, f"h:{handle}"
    return None, f"m:{m.get('id')}"  # last resort: singleton


def _day(m: dict) -> str:
    ts = m.get("timestamp") or ""
    return ts[:10] if len(ts) >= 10 else "unknown-date"


def _iso_week(day_str: str) -> str:
    try:
        d = date.fromisoformat(day_str)
    except ValueError:
        return f"W-{day_str}"
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def _chunks(seq: list[dict], size: int) -> list[list[dict]]:
    if size <= 0:
        return [seq]
    return [seq[i:i + size] for i in range(0, len(seq), size)]


def _sort_ts(msgs: list[dict]) -> list[dict]:
    return sorted(msgs, key=lambda m: (m.get("timestamp") or "", m.get("id") or ""))


def build_batches(messages: Iterable[dict], *, min_batch_msgs: int,
                  max_batch_msgs: int) -> list[Batch]:
    """Group messages into dense person-day/person-week batches.

    Dense days (>= min_batch_msgs) become standalone batches. Thin days merge by
    ISO week into denser batches. All batches chunk to max_batch_msgs.
    """
    # bucket: group_key -> (person_id, channel) -> day -> [messages]
    buckets: dict[str, dict[tuple[str, str], dict[str, list[dict]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(list))
    )
    for m in messages:
        pid, gkey = _correspondent_key(m)
        channel = m.get("channel") or "unknown"
        buckets[gkey][(gkey, channel)][_day(m)].append(m)
        # stash resolved pid on the bucket via the messages themselves (read back later)

    out: list[Batch] = []
    for gkey in sorted(buckets):
        for (bkey, channel), days in sorted(buckets[gkey].items()):
            # Resolve the person_id for this bucket from any message that has one.
            person_id = None
            for daymsgs in days.values():
                for mm in daymsgs:
                    if mm.get("person_id"):
                        person_id = mm["person_id"]
                        break
                if person_id:
                    break

            thin_by_week: dict[str, list[dict]] = defaultdict(list)
            for day_str in sorted(days):
                daymsgs = _sort_ts(days[day_str])
                if len(daymsgs) >= min_batch_msgs:
                    for i, chunk in enumerate(_chunks(daymsgs, max_batch_msgs)):
                        suffix = f":{i}" if i else ""
                        out.append(Batch(
                            batch_key=f"{gkey}:{channel}:{day_str}{suffix}",
                            person_id=person_id, channel=channel, messages=chunk,
                        ))
                else:
                    thin_by_week[_iso_week(day_str)].extend(daymsgs)

            for week in sorted(thin_by_week):
                merged = _sort_ts(thin_by_week[week])
                for i, chunk in enumerate(_chunks(merged, max_batch_msgs)):
                    suffix = f":{i}" if i else ""
                    out.append(Batch(
                        batch_key=f"{gkey}:{channel}:{week}{suffix}",
                        person_id=person_id, channel=channel, messages=chunk,
                    ))
    return out
