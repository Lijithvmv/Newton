"""Unit tests for Newton's pure logic — no model calls, no network.

These cover the two pieces that must never regress silently: the sandbox that keeps the
agent inside the project root, and the action parser that turns model text into a call.
"""

from __future__ import annotations

import pytest

from newton.agent import _extract_action
from newton.tools import ToolBelt, ToolError

# --- action parsing -----------------------------------------------------

def test_extract_plain_object():
    a = _extract_action('{"thought": "hi", "tool": "read_file", "args": {"path": "x"}}')
    assert a["tool"] == "read_file"
    assert a["args"]["path"] == "x"


def test_extract_ignores_surrounding_prose():
    text = 'Sure, here is my action:\n```json\n{"tool": "finish", "args": {"message": "ok"}}\n```'
    a = _extract_action(text)
    assert a["tool"] == "finish"


def test_extract_skips_non_tool_objects():
    # A leading object without a "tool" key must not shadow the real action.
    text = '{"note": "thinking"} then {"tool": "list_dir", "args": {"path": "."}}'
    a = _extract_action(text)
    assert a["tool"] == "list_dir"


def test_extract_returns_none_when_absent():
    assert _extract_action("no json here at all") is None


# --- sandbox ------------------------------------------------------------

def test_write_read_roundtrip(tmp_path):
    belt = ToolBelt(tmp_path)
    belt.write_file(path="sub/note.txt", content="hello")
    assert belt.read_file(path="sub/note.txt") == "hello"


def test_edit_requires_unique_match(tmp_path):
    belt = ToolBelt(tmp_path)
    belt.write_file(path="a.txt", content="x x")
    with pytest.raises(ToolError):
        belt.edit_file(path="a.txt", old="x", new="y")


def test_path_escape_is_refused(tmp_path):
    belt = ToolBelt(tmp_path)
    with pytest.raises(ToolError):
        belt.read_file(path="../../etc/passwd")


def test_grep_finds_match(tmp_path):
    belt = ToolBelt(tmp_path)
    belt.write_file(path="code.py", content="def foo():\n    return 42\n")
    out = belt.grep(pattern="return", path=".")
    assert "code.py" in out and "42" in out


def test_unknown_tool_errors(tmp_path):
    belt = ToolBelt(tmp_path)
    with pytest.raises(ToolError):
        belt.get("nope")
