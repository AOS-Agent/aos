"""Video frame OCR — extract on-screen text from video keyframes.

Pipeline:
  1. Extract keyframes via ffmpeg (1 per N seconds)
  2. Deduplicate near-identical frames (perceptual hash)
  3. OCR unique frames via Surya
  4. Return timestamped text results

Requires: ffmpeg, surya-ocr, imagehash, Pillow
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path


def extract_keyframes(video_path: str, output_dir: str,
                      interval: int = 10) -> list[dict]:
    """Extract one frame every `interval` seconds from a video.

    Returns list of {path, timestamp} sorted by timestamp.
    """
    pattern = os.path.join(output_dir, "frame_%04d.jpg")

    result = subprocess.run(
        ["ffmpeg", "-i", video_path,
         "-vf", f"fps=1/{interval}",
         "-q:v", "2",  # high quality JPEG
         "-loglevel", "error",
         pattern],
        capture_output=True, text=True, timeout=300,
    )

    if result.returncode != 0:
        print(f"  ffmpeg frame extraction failed: {result.stderr}", file=sys.stderr)
        return []

    frames = []
    for f in sorted(Path(output_dir).glob("frame_*.jpg")):
        # Frame number from filename (1-indexed)
        num = int(f.stem.split("_")[1])
        timestamp = (num - 1) * interval
        frames.append({"path": str(f), "timestamp": timestamp})

    return frames


def deduplicate_frames(frames: list[dict], threshold: int = 8) -> list[dict]:
    """Remove near-identical frames using perceptual hashing.

    Frames with a hamming distance <= threshold are considered duplicates.
    Keeps the first occurrence of each unique frame.
    """
    try:
        import imagehash
        from PIL import Image
    except ImportError:
        print("  imagehash/PIL not available, skipping dedup", file=sys.stderr)
        return frames

    unique = []
    seen_hashes = []

    for frame in frames:
        try:
            img = Image.open(frame["path"])
            h = imagehash.phash(img)

            is_dup = False
            for prev_hash in seen_hashes:
                if abs(h - prev_hash) <= threshold:
                    is_dup = True
                    break

            if not is_dup:
                unique.append(frame)
                seen_hashes.append(h)
        except Exception as e:
            print(f"  Hash error on {frame['path']}: {e}", file=sys.stderr)
            continue

    return unique


def ocr_frames(frames: list[dict]) -> list[dict]:
    """Run Surya OCR on a list of frames.

    Returns list of {timestamp, text} for frames that contain text.
    """
    if not frames:
        return []

    try:
        from PIL import Image
        from surya.detection import DetectionPredictor
        from surya.recognition import FoundationPredictor, RecognitionPredictor
    except ImportError as e:
        print(f"  Surya not available: {e}", file=sys.stderr)
        return []

    # Load predictors — FoundationPredictor holds the shared recognition model
    det_predictor = DetectionPredictor()
    foundation = FoundationPredictor()
    rec_predictor = RecognitionPredictor(foundation)

    results = []
    images = []
    timestamps = []

    for frame in frames:
        try:
            img = Image.open(frame["path"])
            images.append(img)
            timestamps.append(frame["timestamp"])
        except Exception as e:
            print(f"  Failed to load {frame['path']}: {e}", file=sys.stderr)

    if not images:
        return []

    # Batch OCR — pass det_predictor to recognition for integrated pipeline
    print(f"  Running Surya OCR on {len(images)} frames...", file=sys.stderr)
    try:
        ocr_results = rec_predictor(images, det_predictor=det_predictor)

        for i, page in enumerate(ocr_results):
            lines = []
            for line in page.text_lines:
                text = line.text.strip()
                if text and len(text) > 2:  # skip noise
                    lines.append(text)

            if lines:
                combined = "\n".join(lines)
                results.append({
                    "timestamp": timestamps[i],
                    "text": combined,
                })
    except Exception as e:
        print(f"  Surya OCR error: {e}", file=sys.stderr)

    return results


def download_video(url: str, output_dir: str) -> str | None:
    """Download video from URL via yt-dlp. Returns path or None."""
    video_path = os.path.join(output_dir, "video.mp4")

    result = subprocess.run(
        ["yt-dlp", "-f", "worst[ext=mp4]",  # smallest video for OCR
         "-o", video_path, "--no-warnings", url],
        capture_output=True, text=True, timeout=120,
    )

    if result.returncode != 0:
        print(f"  Video download failed: {result.stderr}", file=sys.stderr)
        return None

    # yt-dlp may adjust filename
    video_files = list(Path(output_dir).glob("video*"))
    return str(video_files[0]) if video_files else None


def extract_ocr_text(url: str, interval: int = 15,
                     max_frames: int = 30) -> list[dict]:
    """Full OCR pipeline: download video → extract frames → dedup → OCR.

    Args:
        url: Video URL
        interval: Seconds between frame captures (default: 15)
        max_frames: Maximum frames to OCR (default: 30)

    Returns:
        List of {timestamp, text} for frames containing on-screen text.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        # Download video
        print("  Downloading video for OCR...", file=sys.stderr)
        video_path = download_video(url, tmpdir)
        if not video_path:
            return []

        # Extract keyframes
        frames_dir = os.path.join(tmpdir, "frames")
        os.makedirs(frames_dir)
        print(f"  Extracting frames (1 per {interval}s)...", file=sys.stderr)
        frames = extract_keyframes(video_path, frames_dir, interval=interval)
        if not frames:
            return []
        print(f"  Extracted {len(frames)} frames", file=sys.stderr)

        # Deduplicate
        unique_frames = deduplicate_frames(frames)
        print(f"  {len(unique_frames)} unique frames after dedup", file=sys.stderr)

        # Limit to max_frames
        if len(unique_frames) > max_frames:
            # Sample evenly across the video
            step = len(unique_frames) / max_frames
            unique_frames = [unique_frames[int(i * step)] for i in range(max_frames)]

        # OCR
        results = ocr_frames(unique_frames)
        print(f"  OCR found text in {len(results)} frames", file=sys.stderr)

        return results
