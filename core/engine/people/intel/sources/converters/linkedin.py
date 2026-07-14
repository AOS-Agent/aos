#!/usr/bin/env python3
"""Convert LinkedIn GDPR data export to AOS universal JSONL format.

Usage:
    python3 linkedin.py /path/to/linkedin-export/

Reads:
    - Connections.csv (name, email, company, position, connected date)
    - messages.csv (if available — from/to/date/subject/content)

Output: writes linkedin-<date>.jsonl to ~/.aos/imports/
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def convert(export_dir: Path, output_dir: Path) -> int:
    """Convert LinkedIn export to JSONL. Returns message count."""
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / f"linkedin-{datetime.now().strftime('%Y%m%d')}.jsonl"
    count = 0

    # Try messages.csv first
    messages_file = export_dir / "messages.csv"
    if not messages_file.exists():
        # Alternate path in newer exports
        messages_file = export_dir / "Messages" / "messages.csv"

    if messages_file.exists():
        with open(messages_file, newline="", encoding="utf-8") as f, \
             open(output_file, "w") as out:
            reader = csv.DictReader(f)
            for row in reader:
                sender = row.get("FROM", row.get("From", ""))
                content = row.get("CONTENT", row.get("Content", row.get("Body", "")))
                date_str = row.get("DATE", row.get("Date", ""))
                conv_id = row.get("CONVERSATION ID", row.get("ConversationId", ""))

                ts = None
                if date_str:
                    try:
                        ts = datetime.fromisoformat(date_str.replace("Z", "+00:00")).isoformat()
                    except ValueError:
                        pass

                record = {
                    "platform": "linkedin",
                    "conversation_id": conv_id or f"linkedin_{sender}",
                    "conversation_name": sender,
                    "sender": sender,
                    "sender_display": sender,
                    "from_me": False,
                    "timestamp": ts,
                    "text": content,
                    "media_type": None,
                }
                out.write(json.dumps(record, ensure_ascii=False) + "\n")
                count += 1

    if count == 0:
        print(f"No messages found in {export_dir}", file=sys.stderr)

    print(f"Converted {count} LinkedIn messages to {output_file}")
    return count


def main():
    parser = argparse.ArgumentParser(description="Convert LinkedIn export to AOS JSONL")
    parser.add_argument("export_dir", type=Path, help="Path to unzipped LinkedIn export")
    parser.add_argument("--output", type=Path, default=Path.home() / ".aos" / "imports")
    args = parser.parse_args()
    convert(args.export_dir, args.output)


if __name__ == "__main__":
    main()
