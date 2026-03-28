# Memory

ChromaDB-backed MCP server (stdio transport). Provides agents with semantic memory: index documents, store embeddings, and query by meaning. Communicates over stdin/stdout so any MCP-compatible client can use it without a network port.

## Quick Reference
- **Port**: stdio (no port)
- **Restart**: `launchctl kickstart -k gui/$(id -u)/com.aos.memory`
- **Logs**: `~/.aos/logs/memory.log`
- **Config**: `~/.aos/config/memory.yaml`

## Key Files
- `main.py` — MCP server entry point, tool definitions
- `indexer.py` — Chunks and embeds documents into ChromaDB
- `watcher.py` — File system watcher that triggers re-indexing on changes

## Debugging
- Check if running: `pgrep -f "memory/main.py"`
- Tail logs: `tail -f ~/.aos/logs/memory.log`
