"""Append-only chat log with file-locked writes.

Each line is a JSON object: {ts, speaker, addressed_to, body}.
Locking uses fcntl so multiple processes can safely append (operator CLI +
engine running concurrently).
"""
from __future__ import annotations

import datetime
import fcntl
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterator


@dataclass
class Message:
    ts: str
    speaker: str
    addressed_to: str
    body: str

    @classmethod
    def now(cls, speaker: str, addressed_to: str, body: str) -> "Message":
        return cls(
            ts=datetime.datetime.now().isoformat(timespec="seconds"),
            speaker=speaker,
            addressed_to=addressed_to,
            body=body.strip(),
        )


class Chat:
    """Append-only JSONL chat log."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.touch()

    def append(self, msg: Message) -> None:
        with open(self.path, "a") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                f.write(json.dumps(asdict(msg)) + "\n")
                f.flush()
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)

    def read(self) -> list[Message]:
        msgs = []
        if not self.path.exists():
            return msgs
        with open(self.path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                d = json.loads(line)
                msgs.append(Message(**d))
        return msgs

    def tail(self, n: int = 10) -> list[Message]:
        return self.read()[-n:]

    def last(self) -> Message | None:
        msgs = self.read()
        return msgs[-1] if msgs else None

    def watch(self) -> Iterator[Message]:
        """Yield new messages as they're appended. Used by viewers."""
        import time
        seen = 0
        while True:
            msgs = self.read()
            while seen < len(msgs):
                yield msgs[seen]
                seen += 1
            time.sleep(0.5)

    def __len__(self) -> int:
        return sum(1 for _ in open(self.path)) if self.path.exists() else 0
