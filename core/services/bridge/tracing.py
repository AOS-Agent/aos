"""OpenTelemetry tracing for AOS bridge → Arize Phoenix.

Sends structured spans for every Claude CLI invocation so we can
analyze agent performance, tool usage, token costs, and latency
in the Phoenix UI at http://127.0.0.1:6006.
"""

import logging
import os
import time
from contextlib import contextmanager

logger = logging.getLogger("bridge.tracing")

# Phoenix endpoint — only enable if Phoenix is reachable
PHOENIX_ENDPOINT = os.environ.get("PHOENIX_ENDPOINT", "http://127.0.0.1:6006/v1/traces")
PHOENIX_PROJECT = os.environ.get("PHOENIX_PROJECT", "aos-bridge")

_tracer = None
_initialized = False


def init_tracing():
    """Initialize OTel tracing to Phoenix. Safe to call multiple times."""
    global _tracer, _initialized
    if _initialized:
        return _tracer

    _initialized = True
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource

        resource = Resource.create({
            "service.name": "aos-bridge",
            "service.version": "0.1.0",
        })

        provider = TracerProvider(resource=resource)
        exporter = OTLPSpanExporter(endpoint=PHOENIX_ENDPOINT)
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)

        _tracer = trace.get_tracer("aos.bridge", "0.1.0")
        logger.info("Phoenix tracing initialized → %s", PHOENIX_ENDPOINT)
    except ImportError:
        logger.warning("OTel packages not installed — tracing disabled")
        _tracer = None
    except Exception as e:
        logger.warning("Failed to initialize tracing: %s", e)
        _tracer = None

    return _tracer


@contextmanager
def trace_claude_call(agent_name: str, prompt: str, user_key: str,
                      cwd: str = "", metadata: dict | None = None):
    """Context manager that wraps a Claude CLI invocation in an OTel span.

    Usage:
        with trace_claude_call("ops", "check health", "telegram:123") as span:
            # ... run subprocess ...
            span.set_result(response_text, duration_ms, tokens_in, tokens_out, tools_used)
    """
    tracer = init_tracing()

    if tracer is None:
        yield _NoOpSpan()
        return

    from opentelemetry import trace

    with tracer.start_as_current_span(
        name=f"claude.{agent_name}",
        kind=trace.SpanKind.CLIENT,
    ) as span:
        span.set_attribute("agent.name", agent_name)
        span.set_attribute("input.value", prompt[:2000])
        span.set_attribute("user.key", user_key)
        span.set_attribute("working_directory", cwd)
        if metadata:
            for k, v in metadata.items():
                span.set_attribute(f"metadata.{k}", str(v))

        wrapper = _SpanWrapper(span)
        try:
            yield wrapper
        except Exception as e:
            span.set_attribute("error", True)
            span.set_attribute("error.message", str(e))
            raise
        finally:
            # Set output attributes from wrapper
            if wrapper._result is not None:
                span.set_attribute("output.value", wrapper._result[:2000])
            if wrapper._status:
                span.set_attribute("status", wrapper._status)
            if wrapper._duration_ms:
                span.set_attribute("duration_ms", wrapper._duration_ms)
            if wrapper._tokens_in is not None:
                span.set_attribute("llm.token_count.prompt", wrapper._tokens_in)
            if wrapper._tokens_out is not None:
                span.set_attribute("llm.token_count.completion", wrapper._tokens_out)
            if wrapper._tools_used:
                span.set_attribute("tools.used", ",".join(wrapper._tools_used))
                span.set_attribute("tools.count", len(wrapper._tools_used))
            if wrapper._error:
                span.set_attribute("error", True)
                span.set_attribute("error.message", wrapper._error)


class _SpanWrapper:
    """Mutable wrapper so callers can set results after the call completes."""

    def __init__(self, span):
        self._span = span
        self._result: str | None = None
        self._status: str = ""
        self._duration_ms: int = 0
        self._tokens_in: int | None = None
        self._tokens_out: int | None = None
        self._tools_used: list[str] = []
        self._error: str | None = None

    def set_result(self, response: str, duration_ms: int = 0,
                   tokens_in: int = 0, tokens_out: int = 0,
                   tools_used: list[str] | None = None):
        self._result = response
        self._status = "completed"
        self._duration_ms = duration_ms
        self._tokens_in = tokens_in
        self._tokens_out = tokens_out
        self._tools_used = tools_used or []

    def set_error(self, error: str, duration_ms: int = 0):
        self._error = error
        self._status = "failed"
        self._duration_ms = duration_ms

    def add_tool(self, tool_name: str):
        if tool_name not in self._tools_used:
            self._tools_used.append(tool_name)


class _NoOpSpan:
    """Dummy span when tracing is disabled."""

    def set_result(self, *args, **kwargs):
        pass

    def set_error(self, *args, **kwargs):
        pass

    def add_tool(self, *args, **kwargs):
        pass
