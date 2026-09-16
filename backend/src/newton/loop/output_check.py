"""Cheap external verifier for command/tool output — lifted from toolgrad's data-generation validator.

toolgrad rejects tool outputs that "look like errors" (error text, HTML, empty/binary junk) before they
enter its training data. The same heuristic is useful at Newton's runtime: a command can exit 0 and
still have failed — a wrapper that swallows the exit code, a script that prints a traceback but returns
0, a server that returns an error page. Exit code alone misses these; this catches the obvious ones.

Deliberately CONSERVATIVE: it only flags output carrying an unambiguous failure signature, so a
legitimate exit-0 command is never failed by mistake (false positives would break the loop by sending
good steps into needless repair). When unsure, it says "not error-shaped".
"""

from __future__ import annotations

# Unambiguous failure signatures that can appear even when a process exits 0.
_ERROR_MARKERS = (
    "traceback (most recent call last)",
    "modulenotfounderror",
    "importerror:",
    "command not found",
    "no such file or directory",
    "is not recognized as an internal or external command",  # Windows shell
    "cannot find the path specified",
    "segmentation fault",
    "unhandled exception",
)


def error_shaped(text: str) -> bool:
    """True only when `text` carries an unambiguous failure signature (a real error/HTML error page),
    so it is safe to treat an exit-0 command with this output as a failure. Empty output is NOT
    error-shaped here — many valid commands print nothing — so callers decide that separately."""
    t = (text or "").strip()
    if not t:
        return False
    head = t[:200].lstrip().lower()
    if head.startswith("<!doctype html") or head.startswith("<html"):
        return True                                   # an HTML (error) page where text was expected
    low = t.lower()
    return any(marker in low for marker in _ERROR_MARKERS)
