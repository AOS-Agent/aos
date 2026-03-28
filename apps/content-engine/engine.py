"""Content Engine — the main orchestrator.

Usage:
    from engine import extract, extract_metadata_only

    # Full extraction (metadata + transcription + vault)
    result = extract("https://instagram.com/reel/ABC123", save_to_vault=True)

    # Metadata only (fast, ~2s)
    result = extract_metadata_only("https://youtube.com/watch?v=XYZ")
"""

import sys

from detect import detect_platform
from models import ExtractionResult, SourceContext
from platforms import get_handler
from transcribe import transcribe_url
from dedup import is_processed, mark_processed
from vault import save_vault_note


def extract_metadata_only(url: str) -> ExtractionResult | None:
    """Tier 1: Fast metadata extraction (~2s, no transcription).

    Returns ExtractionResult with metadata populated, transcript=None.
    """
    platform, content_id, content_type = detect_platform(url)
    if not platform:
        print(f"Unsupported URL: {url}", file=sys.stderr)
        return None

    handler = get_handler(platform)
    if not handler:
        print(f"No handler for platform: {platform}", file=sys.stderr)
        return None

    print(f"Extracting metadata: {platform} / {content_id}")
    result = handler.extract_metadata(url, content_id, content_type)
    return result


def extract(url: str, save_to_vault: bool = False, vault_dir: str | None = None,
            whisper_model: str = "medium", skip_transcript: bool = False,
            force: bool = False, source_context: SourceContext | None = None,
            ) -> ExtractionResult | None:
    """Full extraction pipeline: detect → metadata → transcribe → vault.

    Args:
        url: Any supported social media URL
        save_to_vault: Save result as vault markdown note
        vault_dir: Custom vault directory (default: ~/vault/materials/content-extract/)
        whisper_model: Whisper model size for transcription
        skip_transcript: Only extract metadata, skip transcription
        force: Process even if URL was already processed
        source_context: Who sent this URL and where (for message-sourced links)

    Returns:
        ExtractionResult with all available data, or None if URL unsupported.
    """
    # Detect platform
    platform, content_id, content_type = detect_platform(url)
    if not platform:
        print(f"Unsupported URL: {url}", file=sys.stderr)
        return None

    # Dedup check
    if not force and is_processed(content_id, platform):
        print(f"Already processed: {platform}/{content_id} (use force=True to reprocess)")
        return None

    # Get platform handler
    handler = get_handler(platform)
    if not handler:
        print(f"No handler for platform: {platform}", file=sys.stderr)
        return None

    # Step 1: Extract metadata
    print(f"[{platform}] Extracting metadata for {content_id}...")
    result = handler.extract_metadata(url, content_id, content_type)

    # Attach source context if provided
    if source_context:
        result.source_context = source_context

    # Step 2: Transcribe (if content has audio and transcription not skipped)
    if not skip_transcript and result.has_audio:
        print(f"[{platform}] Transcribing ({result.duration}s audio)...")
        transcript, source = transcribe_url(
            url, platform=platform, content_id=content_id, model=whisper_model
        )
        result.transcript = transcript
        result.transcript_source = source
        if transcript:
            print(f"  Transcribed {len(transcript)} chars via {source}")
        else:
            print("  No transcript available", file=sys.stderr)

    # Step 3: Save to vault
    if save_to_vault:
        vault_path = save_vault_note(result, vault_dir=vault_dir)
        print(f"  Vault note: {vault_path}")

    # Step 4: Record as processed
    mark_processed(url, content_id, platform,
                   tier="metadata" if skip_transcript else "deep",
                   vault_path=result.vault_path or "")

    return result
