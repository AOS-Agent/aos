#!/usr/bin/env python3
"""Convert Facebook/Instagram GDPR data export to AOS universal JSONL format.

Usage:
    python3 meta_export.py /path/to/facebook-export/ --platform facebook
    python3 meta_export.py /path/to/instagram-export/ --platform instagram

Both platforms use the same JSON format under messages/inbox/<conversation>/message_1.json.
Output: writes <platform>-<date>.jsonl to ~/.aos/imports/

Known gotcha: Facebook encodes characters as Latin-1 bytes in UTF-8 JSON.
This converter handles that transparently.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def _fix_facebook_encoding(text: str) -> str:
    """Fix Facebook's broken encoding (Latin-1 bytes stored as UTF-8)."""
    try:
        return text.encode("latin-1").decode("utf-8")
    except (UnicodeDecodeError, UnicodeEncodeError):
        return text


def convert(export_dir: Path, platform: str, output_dir: Path) -> int:
    """Convert a Meta export directory to JSONL.

    Returns number of messages converted.
    """
    inbox = export_dir / "messages" / "inbox"
    if not inbox.exists():
        # Try alternate paths
        for alt in ["your_activity_across_facebook/messages/inbox",
                     "your_instagram_activity/messages/inbox"]:
            alt_path = export_dir / alt
            if alt_path.exists():
                inbox = alt_path
                break

    if not inbox.exists():
        print(f"No inbox found at {export_dir}", file=sys.stderr)
        return 0

    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / f"{platform}-{datetime.now().strftime('%Y%m%d')}.jsonl"

    count = 0
    with open(output_file, "w") as out:
        for conv_dir in sorted(inbox.iterdir()):
            if not conv_dir.is_dir():
                continue

            conv_name = conv_dir.name
            for msg_file in sorted(conv_dir.glob("message_*.json")):
                try:
                    data = json.loads(msg_file.read_bytes())
                except Exception:
                    continue

                participants = [p.get("name", "") for p in data.get("participants", [])]
                # The conversation name for DMs is the other participant
                display_name = participants[0] if len(participants) == 1 else conv_name

                for msg in data.get("messages", []):
                    sender = _fix_facebook_encoding(msg.get("sender_name", ""))
                    content = _fix_facebook_encoding(msg.get("content", ""))
                    ts_ms = msg.get("timestamp_ms", 0)

                    media_type = None
                    if msg.get("photos"):
                        media_type = "photo"
                    elif msg.get("videos"):
                        media_type = "video"
                    elif msg.get("audio_files"):
                        media_type = "audio"
                    elif msg.get("share"):
                        media_type = "link"

                    record = {
                        "platform": platform,
                        "conversation_id": conv_name,
                        "conversation_name": _fix_facebook_encoding(display_name),
                        "sender": sender,
                        "sender_display": sender,
                        "from_me": False,  # Will be updated by the user
                        "timestamp": datetime.fromtimestamp(
                            ts_ms / 1000, tz=timezone.utc
                        ).isoformat() if ts_ms else None,
                        "text": content,
                        "media_type": media_type,
                    }
                    out.write(json.dumps(record, ensure_ascii=False) + "\n")
                    count += 1

    print(f"Converted {count} messages to {output_file}")
    return count


def main():
    parser = argparse.ArgumentParser(description="Convert Meta export to AOS JSONL")
    parser.add_argument("export_dir", type=Path, help="Path to unzipped export directory")
    parser.add_argument("--platform", choices=["facebook", "instagram"], default="facebook")
    parser.add_argument("--output", type=Path, default=Path.home() / ".aos" / "imports")
    args = parser.parse_args()

    convert(args.export_dir, args.platform, args.output)


if __name__ == "__main__":
    main()
