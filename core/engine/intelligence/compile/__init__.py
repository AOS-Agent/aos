"""intelligence.compile — Compilation engine (Pass 2, Haiku).

Takes an ExtractionResult from the content router and produces a
CompilationResult: summary, concepts, topic, entities, related captures,
stage suggestion. This is the per-capture, on-write pass.

Cross-capture patterns (Pass 3, Sonnet) happen overnight in
intelligence.lint (Part 10). Deep synthesis drafts (Pass 4, Opus) are
on-demand from the operator.

Design notes:
    - All LLM calls go through core.engine.execution.router (ExecutionRouter),
      which already handles Claude Code subscription + OpenRouter gateway +
      Anthropic API + local Ollama. Credentials via Keychain. No reinvention.
    - Templates per capture type are in templates/ (tweet, blog, video, ...)
      — Nate Jones discipline: different shapes force different thinking.
    - Prompts read existing topic index files so the LLM "pre-knows" the
      landscape at write-time — Karpathy's trick.
"""

from .llm import complete, LLMResponse, LLMError

__all__ = [
    "complete",
    "LLMResponse",
    "LLMError",
]

# Lazy re-exports — engine.py is imported on demand to avoid pulling
# the whole compile pipeline into every consumer.
def __getattr__(name: str):
    if name in ("compile_capture", "CompilationResult", "CompilationError"):
        from . import engine as _engine
        return getattr(_engine, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
