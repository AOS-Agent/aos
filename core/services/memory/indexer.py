"""Core indexer — loads workspace files, chunks, embeds, stores in ChromaDB.

Uses ChromaDB's built-in embedding (onnxruntime + all-MiniLM-L6-v2).
No PyTorch or LlamaIndex needed — keeps RAM under 500MB.
"""

import re
from pathlib import Path
from typing import Optional

import chromadb

# Workspace root (two levels up from apps/memory/)
WORKSPACE = Path(__file__).resolve().parent.parent.parent

CHROMA_PATH = WORKSPACE / "data" / "memory" / "chromadb"

WATCH_GLOBS = [
    "config/*.yaml",
    "logs/*.md",
    "specs/*.md",
    "docs/*.md",
    ".claude/agents/*.md",
    ".claude/commands/*.md",
    ".claude/skills/*/SKILL.md",
    "CLAUDE.md",
    "justfile",
]

COLLECTION_NAME = "workspace"

# Chunking limits (in characters — ~4 chars/token)
MAX_CHUNK_CHARS = 2000


def _resolve_paths() -> list[Path]:
    """Resolve all watch globs to actual file paths."""
    paths = []
    for glob_pattern in WATCH_GLOBS:
        paths.extend(WORKSPACE.glob(glob_pattern))
    return sorted(set(paths))


def _detect_file_type(path: Path) -> str:
    if path.suffix in (".md", ".markdown"):
        return "markdown"
    if path.suffix in (".yaml", ".yml"):
        return "yaml"
    return "text"


def _split_long_text(text: str, max_chars: int = MAX_CHUNK_CHARS) -> list[str]:
    """Split text that exceeds max_chars by paragraph boundaries."""
    if len(text) <= max_chars:
        return [text]

    parts = []
    paragraphs = text.split("\n\n")
    current = ""

    for para in paragraphs:
        if current and len(current) + len(para) + 2 > max_chars:
            parts.append(current.strip())
            current = para
        else:
            current = current + "\n\n" + para if current else para

    if current.strip():
        parts.append(current.strip())

    return parts if parts else [text[:max_chars]]


def _chunk_markdown(text: str, source: str) -> list[dict]:
    """Split markdown by headers, then by size if needed."""
    chunks = []
    sections = re.split(r'(?=^#{1,3}\s)', text, flags=re.MULTILINE)

    for section in sections:
        section = section.strip()
        if not section:
            continue
        first_line = section.split('\n', 1)[0]
        title = first_line.lstrip('#').strip() if first_line.startswith('#') else ""

        for part in _split_long_text(section):
            chunks.append({
                "text": part,
                "metadata": {
                    "source": source,
                    "section": title,
                    "file_type": "markdown",
                }
            })

    return chunks if chunks else [{"text": text, "metadata": {"source": source, "section": "", "file_type": "markdown"}}]


def _chunk_file(path: Path) -> list[dict]:
    """Chunk a file based on its type."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []

    if not text.strip():
        return []

    rel = str(path.relative_to(WORKSPACE))
    file_type = _detect_file_type(path)
    mtime = path.stat().st_mtime

    if file_type == "markdown":
        chunks = _chunk_markdown(text, rel)
    else:
        prefixed = f"# {rel}\n\n{text}"
        parts = _split_long_text(prefixed)
        chunks = [{
            "text": part,
            "metadata": {
                "source": rel,
                "section": "",
                "file_type": file_type,
            }
        } for part in parts]

    for chunk in chunks:
        chunk["metadata"]["mtime"] = mtime

    return chunks


class MemoryIndexer:
    def __init__(self):
        self._client = chromadb.PersistentClient(path=str(CHROMA_PATH))
        # Clean up stale collections on startup — prevents UUID mismatch errors
        # after crashes or unclean shutdowns
        try:
            self._collection = self._client.get_or_create_collection(COLLECTION_NAME)
            # Verify the collection is usable
            self._collection.count()
        except Exception:
            # Collection corrupted — nuke and recreate
            try:
                self._client.delete_collection(COLLECTION_NAME)
            except Exception:
                pass
            self._collection = self._client.get_or_create_collection(COLLECTION_NAME)

    def index_workspace(self) -> dict:
        """Full reindex of all workspace files."""
        self._client.delete_collection(COLLECTION_NAME)
        self._collection = self._client.get_or_create_collection(COLLECTION_NAME)

        paths = _resolve_paths()
        all_ids = []
        all_docs = []
        all_metas = []
        file_count = 0

        for path in paths:
            chunks = _chunk_file(path)
            if chunks:
                file_count += 1
                for i, chunk in enumerate(chunks):
                    chunk_id = f"{chunk['metadata']['source']}::{i}"
                    all_ids.append(chunk_id)
                    all_docs.append(chunk["text"])
                    all_metas.append(chunk["metadata"])

        # ChromaDB handles embedding internally
        if all_docs:
            self._collection.add(
                ids=all_ids,
                documents=all_docs,
                metadatas=all_metas,
            )

        return {
            "files_indexed": file_count,
            "chunks_stored": self._collection.count(),
        }

    def reindex_file(self, rel_path: str) -> dict:
        """Reindex a single file (incremental update)."""
        path = WORKSPACE / rel_path

        # Delete existing chunks for this file
        existing = self._collection.get(where={"source": rel_path})
        if existing["ids"]:
            self._collection.delete(ids=existing["ids"])

        if not path.exists():
            return {"action": "deleted", "source": rel_path}

        chunks = _chunk_file(path)
        if not chunks:
            return {"action": "empty", "source": rel_path}

        ids = []
        docs = []
        metas = []
        for i, chunk in enumerate(chunks):
            ids.append(f"{rel_path}::{i}")
            docs.append(chunk["text"])
            metas.append(chunk["metadata"])

        self._collection.add(ids=ids, documents=docs, metadatas=metas)
        return {"action": "reindexed", "source": rel_path, "chunks": len(ids)}

    def search(self, query: str, top_k: int = 5, file_filter: Optional[str] = None) -> list[dict]:
        """Semantic search across indexed files."""
        if self._collection.count() == 0:
            return []

        # Fetch extra results when filtering, then trim to top_k
        fetch_k = top_k * 3 if file_filter else top_k
        results = self._collection.query(
            query_texts=[query],
            n_results=fetch_k,
        )

        out = []
        for i, doc_id in enumerate(results["ids"][0]):
            meta = results["metadatas"][0][i]
            source = meta.get("source", "")
            if file_filter and file_filter not in source:
                continue
            distance = results["distances"][0][i] if results.get("distances") else None
            # ChromaDB returns L2 distance — convert to similarity score
            score = round(1 / (1 + distance), 4) if distance is not None else None
            out.append({
                "source": source,
                "section": meta.get("section", ""),
                "score": score,
                "text": results["documents"][0][i][:500],
            })
            if len(out) >= top_k:
                break
        return out

    def recall(self, file_path: str) -> Optional[str]:
        """Get full content of a specific file."""
        path = WORKSPACE / file_path
        if path.exists():
            return path.read_text(encoding="utf-8", errors="replace")
        return None

    def get_status(self) -> dict:
        """Index statistics."""
        paths = _resolve_paths()
        chroma_size = sum(
            f.stat().st_size for f in CHROMA_PATH.rglob("*") if f.is_file()
        ) if CHROMA_PATH.exists() else 0

        return {
            "indexed_chunks": self._collection.count(),
            "watchable_files": len(paths),
            "storage_bytes": chroma_size,
            "storage_mb": round(chroma_size / (1024 * 1024), 2),
            "chroma_path": str(CHROMA_PATH),
        }
