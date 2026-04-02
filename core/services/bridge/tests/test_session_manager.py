"""Tests for session_manager event parsing."""

import json
import sys
from pathlib import Path

# Add bridge directory to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from session_manager import (
    ApiRetry,
    RateLimit,
    SessionInit,
    SessionResult,
    TextComplete,
    TextDelta,
    ToolResult,
    ToolStart,
    detect_dispatch,
    parse_event,
)


def test_parse_text_delta():
    raw = json.dumps({
        "type": "stream_event",
        "event": {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "Hello"},
        },
        "session_id": "abc",
    })
    event = parse_event(raw)
    assert isinstance(event, TextDelta)
    assert event.text == "Hello"


def test_parse_tool_start():
    raw = json.dumps({
        "type": "stream_event",
        "event": {
            "type": "content_block_start",
            "index": 0,
            "content_block": {
                "type": "tool_use",
                "id": "toolu_1",
                "name": "Read",
                "input": {"file_path": "/foo/bar.py"},
            },
        },
        "session_id": "abc",
    })
    event = parse_event(raw)
    assert isinstance(event, ToolStart)
    assert event.name == "Read"
    assert "bar.py" in event.input_preview


def test_parse_tool_result():
    raw = json.dumps({
        "type": "user",
        "message": {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "content": "file contents here",
                    "is_error": False,
                    "tool_use_id": "toolu_1",
                }
            ],
        },
    })
    event = parse_event(raw)
    assert isinstance(event, ToolResult)
    assert not event.is_error
    assert "file contents" in event.preview


def test_parse_session_init():
    raw = json.dumps({
        "type": "system",
        "subtype": "init",
        "session_id": "abc-123",
        "model": "claude-opus-4-6",
        "tools": ["Read", "Bash"],
    })
    event = parse_event(raw)
    assert isinstance(event, SessionInit)
    assert event.session_id == "abc-123"
    assert event.model == "claude-opus-4-6"


def test_parse_api_retry():
    raw = json.dumps({
        "type": "system",
        "subtype": "api_retry",
        "attempt": 2,
        "max_retries": 5,
        "retry_delay_ms": 3000,
        "error": "server_error",
    })
    event = parse_event(raw)
    assert isinstance(event, ApiRetry)
    assert event.attempt == 2
    assert event.delay_ms == 3000


def test_parse_rate_limit():
    raw = json.dumps({
        "type": "rate_limit_event",
        "rate_limit_info": {
            "status": "allowed",
            "resetsAt": 1773882000,
        },
    })
    event = parse_event(raw)
    assert isinstance(event, RateLimit)
    assert event.status == "allowed"


def test_parse_result():
    raw = json.dumps({
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "session_id": "abc",
        "result": "Done!",
        "duration_ms": 1500,
        "total_cost_usd": 0.05,
        "num_turns": 2,
        "usage": {
            "input_tokens": 100,
            "cache_read_input_tokens": 50,
            "output_tokens": 25,
        },
    })
    event = parse_event(raw)
    assert isinstance(event, SessionResult)
    assert event.cost_usd == 0.05
    assert event.input_tokens == 150
    assert event.output_tokens == 25
    assert event.text == "Done!"
    assert not event.is_error


def test_parse_result_error():
    raw = json.dumps({
        "type": "result",
        "subtype": "error",
        "is_error": True,
        "session_id": "",
        "result": "Something failed",
        "duration_ms": 100,
        "total_cost_usd": 0,
        "num_turns": 0,
        "usage": {"input_tokens": 0, "output_tokens": 0},
    })
    event = parse_event(raw)
    assert isinstance(event, SessionResult)
    assert event.is_error


def test_parse_text_complete():
    raw = json.dumps({
        "type": "assistant",
        "message": {
            "content": [{"type": "text", "text": "Full response here"}],
        },
    })
    event = parse_event(raw)
    assert isinstance(event, TextComplete)
    assert event.text == "Full response here"


def test_parse_invalid_json():
    assert parse_event("not json") is None


def test_parse_unknown_event():
    assert parse_event('{"type": "unknown"}') is None


def test_parse_empty_assistant():
    """Assistant message with only tool_use blocks (no text) returns None."""
    raw = json.dumps({
        "type": "assistant",
        "message": {
            "content": [
                {"type": "tool_use", "id": "toolu_1", "name": "Read", "input": {}}
            ],
        },
    })
    assert parse_event(raw) is None


def test_detect_dispatch_ask():
    agent, msg = detect_dispatch("ask ops to check health")
    # Will only match if 'ops' agent exists, otherwise returns None
    # This tests the function doesn't crash
    assert isinstance(msg, str)


def test_detect_dispatch_no_match():
    agent, msg = detect_dispatch("hello world")
    assert agent is None
    assert msg == "hello world"
