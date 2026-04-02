"""MCP server for persistent memory — 4 tools over stdio transport."""

import setproctitle; setproctitle.setproctitle("aos-memory")

import json
from typing import Optional

from indexer import MemoryIndexer
from mcp.server.fastmcp import FastMCP
from watcher import start_watcher

# Initialize
mcp = FastMCP("agent-memory", log_level="WARNING")
indexer = MemoryIndexer()

# Start file watcher
_watcher_thread = start_watcher(indexer)


@mcp.tool()
def memory_search(query: str, top_k: int = 5, file_filter: Optional[str] = None) -> str:
    """Search workspace memory semantically. Returns the most relevant chunks.

    Args:
        query: Natural language search query
        top_k: Number of results to return (default 5)
        file_filter: Optional substring to filter by source file path
    """
    results = indexer.search(query, top_k=top_k, file_filter=file_filter)
    if not results:
        return "No results found. Try reindexing with memory_reindex()."
    return json.dumps(results, indent=2)


@mcp.tool()
def memory_recall(file_path: str) -> str:
    """Get full content of a specific workspace file.

    Args:
        file_path: Relative path from workspace root (e.g. 'config/state.yaml')
    """
    content = indexer.recall(file_path)
    if content is None:
        return f"File not found: {file_path}"
    return content


@mcp.tool()
def memory_status() -> str:
    """Get index statistics: chunk count, file count, storage size."""
    status = indexer.get_status()
    return json.dumps(status, indent=2)


@mcp.tool()
def memory_reindex(path: Optional[str] = None) -> str:
    """Force reindex. If path is given, reindex that file only. Otherwise reindex everything.

    Args:
        path: Optional relative file path to reindex. Omit for full reindex.
    """
    if path:
        result = indexer.reindex_file(path)
    else:
        result = indexer.index_workspace()
    return json.dumps(result, indent=2)


if __name__ == "__main__":
    mcp.run(transport="stdio")
