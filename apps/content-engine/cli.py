#!/usr/bin/env python3
"""Content Engine CLI — extract content from any social media URL.

Usage:
    python3 cli.py <url>                          # Full extraction
    python3 cli.py <url> --metadata-only          # Fast metadata only
    python3 cli.py <url> --vault                  # Extract + save to vault
    python3 cli.py <url> --vault-dir ~/custom/    # Custom vault location
    python3 cli.py --batch urls.txt --vault       # Batch process URLs from file
"""

import argparse
import json
import sys
from pathlib import Path

from engine import extract, extract_metadata_only


def main():
    parser = argparse.ArgumentParser(description="Content Engine — social media extraction")
    parser.add_argument("url", nargs="?", help="URL to extract")
    parser.add_argument("--metadata-only", action="store_true",
                        help="Only extract metadata, skip transcription")
    parser.add_argument("--vault", action="store_true",
                        help="Save result as vault markdown note")
    parser.add_argument("--vault-dir", default=None,
                        help="Custom vault directory")
    parser.add_argument("--model", default="medium",
                        help="Whisper model size (tiny/base/small/medium/large)")
    parser.add_argument("--batch", default=None,
                        help="File containing URLs (one per line)")
    parser.add_argument("--force", action="store_true",
                        help="Reprocess even if already extracted")
    parser.add_argument("--no-ocr", action="store_true",
                        help="Skip video frame OCR (faster)")
    parser.add_argument("--json", action="store_true", dest="json_output",
                        help="Output as JSON")
    args = parser.parse_args()

    if not args.url and not args.batch:
        parser.print_help()
        sys.exit(1)

    # Collect URLs to process
    urls = []
    if args.batch:
        batch_file = Path(args.batch)
        if not batch_file.exists():
            print(f"Batch file not found: {args.batch}", file=sys.stderr)
            sys.exit(1)
        for line in batch_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                urls.append(line)
        print(f"Batch: {len(urls)} URLs to process")
    else:
        urls.append(args.url)

    results = []
    for url in urls:
        if args.metadata_only:
            result = extract_metadata_only(url)
        else:
            result = extract(
                url,
                save_to_vault=args.vault,
                vault_dir=args.vault_dir,
                whisper_model=args.model,
                skip_transcript=args.metadata_only,
                skip_ocr=args.no_ocr,
                force=args.force,
            )

        if result:
            results.append(result)

            if args.json_output:
                print(json.dumps(result.to_dict(), indent=2))
            else:
                print(f"\n{'='*60}")
                print(f"Platform:  {result.platform} / {result.content_type}")
                print(f"Author:    {result.author} ({result.author_id})")
                print(f"Title:     {result.title[:80]}")
                print(f"Duration:  {result.duration}s")
                if result.engagement.to_dict():
                    eng = result.engagement.to_dict()
                    print(f"Engagement: {', '.join(f'{k}={v:,}' for k, v in eng.items())}")
                if result.description:
                    desc = result.description[:200]
                    print(f"Caption:   {desc}{'...' if len(result.description) > 200 else ''}")
                if result.has_transcript:
                    print(f"Transcript: {len(result.transcript)} chars ({result.transcript_source})")
                if result.vault_path:
                    print(f"Vault:     {result.vault_path}")
                if result.needs_fallback:
                    print(f"FALLBACK_NEEDED: {result.fallback_reason}")
                print(f"{'='*60}")
        else:
            print(f"Failed or skipped: {url}", file=sys.stderr)

    print(f"\nProcessed {len(results)}/{len(urls)} URLs")


if __name__ == "__main__":
    main()
