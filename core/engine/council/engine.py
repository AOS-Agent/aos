"""Council engine — main loop.

For each turn:
  1. Scheduler picks the next speaker
  2. Build a prompt: persona body + chat tail + addressing context
  3. Invoke `claude -p` with that prompt
  4. Parse addressing tag from reply
  5. Append to chat.jsonl
  6. Repeat until @close, max turns, or operator interrupt

Why `claude -p` over the Anthropic API:
  - No API key management — uses operator's existing auth
  - Each turn is a fresh process / fresh context window — no contamination
  - Cancellation via signal works natively
  - Replaces the cmux-pane runtime entirely (no paste-buffer corruption,
    no completion-verb regex, no idle-state detection)
"""
from __future__ import annotations
import subprocess
import re
import sys
import datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .chat import Chat, Message
from .persona import Persona, load_persona
from .scheduler import Scheduler, parse_addressing
from .synthesis import synthesize

COUNCIL_ROOT = Path.home() / ".aos" / "data" / "councils"
END_MARKER = "=== END ==="


def _slugify(s: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9\s-]", "", s).strip().lower()
    s = re.sub(r"[\s_-]+", "-", s)
    return s[:50] or "untitled"


def build_protocol(persona: Persona, chat: Chat, others: list[str]) -> str:
    """Compose the full turn prompt for a persona."""
    tail = chat.tail(10)
    log_lines = []
    for m in tail:
        addr = f" → @{m.addressed_to}" if m.addressed_to else ""
        log_lines.append(f"[{m.speaker}{addr}]\n{m.body}")
    log = "\n\n".join(log_lines) if log_lines else "(no messages yet)"

    last = chat.last()
    last_speaker = last.speaker if last else "operator"
    last_body = last.body if last else "(start the council)"

    others_str = ", ".join(f"@{p}" for p in others)

    return f"""You are {persona.id.upper()} in a multi-agent council. Stay in character.

YOUR LENS:
{persona.lens}

{persona.body}

COUNCIL PROTOCOL:
- You are in an open chat with three peers: {others_str}.
- The chat log is shown below. Engage the most recent point.
- Keep replies SHORT: 2-5 sentences, prose. No bullets, no headers.
- You may directly address other agents inline ("Builder, your X is wrong because...").
- EVERY reply MUST end with exactly one addressing tag on its own line:
    @<persona-id>  — hand the token to a specific peer
    @all           — open the floor (oldest non-speaker takes it)
    @close         — call the question (use only when convergence is reached)
- Then end your turn with the line: {END_MARKER}

CHAT LOG SO FAR:
{log}

You have been addressed by @{last_speaker}. Their last message:
"{last_body}"

Your turn. Respond now."""


@dataclass
class Council:
    id: str
    topic: str
    personas: list[str]
    chat: Chat
    root: Path
    max_turns: int = 12
    on_turn: Callable[[Message], None] | None = None

    @classmethod
    def convene(
        cls,
        topic: str,
        personas: list[str] | None = None,
        seed: str | None = None,
        first_speaker: str | None = None,
        max_turns: int = 12,
        on_turn: Callable[[Message], None] | None = None,
    ) -> "Council":
        personas = personas or ["architect", "builder", "skeptic", "dreamer"]
        # Validate personas exist
        for p in personas:
            load_persona(p)
        id_ = _slugify(topic)
        root = COUNCIL_ROOT / id_
        root.mkdir(parents=True, exist_ok=True)
        chat = Chat(root / "chat.jsonl")
        # If empty, seed with operator's opening message
        if len(chat) == 0:
            opening = seed or f"Council, the topic is: {topic}. Open chat — engage freely."
            addressee = first_speaker or personas[0]
            chat.append(Message.now(speaker="operator", addressed_to=addressee, body=opening))
        # Save topic metadata
        (root / "topic.txt").write_text(topic)
        (root / "personas.txt").write_text("\n".join(personas))
        return cls(
            id=id_,
            topic=topic,
            personas=personas,
            chat=chat,
            root=root,
            max_turns=max_turns,
            on_turn=on_turn,
        )

    @classmethod
    def resume(cls, id_: str, max_turns: int = 12, on_turn=None) -> "Council":
        root = COUNCIL_ROOT / id_
        if not root.exists():
            raise FileNotFoundError(f"No council at {root}")
        topic = (root / "topic.txt").read_text().strip()
        personas = (root / "personas.txt").read_text().strip().splitlines()
        chat = Chat(root / "chat.jsonl")
        return cls(id=id_, topic=topic, personas=personas, chat=chat, root=root,
                   max_turns=max_turns, on_turn=on_turn)

    def _claude_p(self, prompt: str, timeout: int = 180, retries: int = 2) -> str:
        """Invoke `claude -p` with the prompt. Returns the response body.

        Retries transient failures (e.g. 'claude native binary not installed' race
        conditions, network blips). Total max wait ≈ timeout * (retries + 1).
        """
        import time as _time
        last_err = None
        for attempt in range(retries + 1):
            try:
                proc = subprocess.run(
                    ["claude", "-p", prompt],
                    capture_output=True, text=True, timeout=timeout,
                )
                if proc.returncode == 0:
                    out = proc.stdout.strip()
                    if out:
                        return out
                    last_err = f"empty stdout (rc=0)"
                else:
                    last_err = f"rc={proc.returncode}: {proc.stderr[:300]}"
            except subprocess.TimeoutExpired:
                last_err = "timeout"
            if attempt < retries:
                _time.sleep(2 + attempt * 3)  # backoff
        raise RuntimeError(f"claude -p failed after {retries+1} attempts: {last_err}")

    def _clean_reply(self, raw: str) -> str:
        # Strip trailing END marker
        return re.sub(rf"\s*{re.escape(END_MARKER)}\s*$", "", raw).strip()

    def synthesize(self) -> dict:
        """Generate a synthesis memo + vault decision doc. Idempotent (overwrites)."""
        return synthesize(self.id, self.topic, self.personas, self.chat)

    def run(self, auto_synthesize: bool = True) -> list[Message]:
        """Run the loop until @close, max_turns, or speaker resolution fails.

        If auto_synthesize is True (default), writes a vault decision memo when done.
        """
        scheduler = Scheduler(self.chat, self.personas)
        new_messages = []
        for turn in range(1, self.max_turns + 1):
            next_id = scheduler.next_speaker()
            if next_id is None:
                print(f"[council:{self.id}] No initial speaker — chat is empty.", file=sys.stderr)
                break
            if next_id == "close":
                print(f"[council:{self.id}] @close received. Adjourned at turn {turn}.", file=sys.stderr)
                break

            persona = load_persona(next_id)
            others = [p for p in self.personas if p != next_id]
            prompt = build_protocol(persona, self.chat, others)

            print(f"[council:{self.id}] Turn {turn}: @{next_id} speaking...", file=sys.stderr)
            try:
                raw = self._claude_p(prompt)
            except subprocess.TimeoutExpired:
                print(f"[council:{self.id}] TIMEOUT on @{next_id}. Adjourning.", file=sys.stderr)
                break
            except Exception as e:
                print(f"[council:{self.id}] ERROR on @{next_id}: {e}", file=sys.stderr)
                break

            body = self._clean_reply(raw)
            addr = parse_addressing(body, self.personas)
            msg = Message.now(speaker=next_id, addressed_to=addr, body=body)
            self.chat.append(msg)
            new_messages.append(msg)
            if self.on_turn:
                self.on_turn(msg)

            if addr == "close":
                print(f"[council:{self.id}] @{next_id} called @close. Adjourned.", file=sys.stderr)
                break

        if auto_synthesize and new_messages:
            try:
                result = self.synthesize()
                if result.get("memo_path"):
                    print(f"[council:{self.id}] Synthesis: {result['memo_path']}", file=sys.stderr)
                    # Best-effort Telegram delivery
                    try:
                        from .notify import send_to_telegram
                        notify_result = send_to_telegram(result["memo_path"], self.topic, self.id)
                        if notify_result.get("ok"):
                            print(f"[council:{self.id}] Telegram delivered.", file=sys.stderr)
                        else:
                            print(f"[council:{self.id}] Telegram skipped: {notify_result.get('reason', 'unknown')}", file=sys.stderr)
                    except Exception as e:
                        print(f"[council:{self.id}] Telegram failed: {e}", file=sys.stderr)
            except Exception as e:
                print(f"[council:{self.id}] Synthesis failed: {e}", file=sys.stderr)

        return new_messages

    def convene_background(self) -> int:
        """Fire-and-forget — fork the council into a background process.

        Returns the PID. The parent can return immediately; the council runs,
        synthesizes, and writes the vault memo without blocking.
        """
        import os as _os
        pid = _os.fork()
        if pid == 0:
            # Child
            try:
                # Detach from parent terminal
                _os.setsid()
                # Reopen stdio to log files
                log_dir = self.root / "logs"
                log_dir.mkdir(exist_ok=True)
                log_path = log_dir / f"run-{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}.log"
                with open(log_path, "w") as lf:
                    _os.dup2(lf.fileno(), 1)
                    _os.dup2(lf.fileno(), 2)
                self.run(auto_synthesize=True)
            finally:
                _os._exit(0)
        return pid
