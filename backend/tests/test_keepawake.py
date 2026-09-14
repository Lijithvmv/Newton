"""keep_awake must be a safe, non-raising context manager: a pass-through when disabled, and on the
target OS it holds off sleep and always releases. It must never break a build."""

from __future__ import annotations

from newton.loop.keepawake import keep_awake


def test_disabled_is_a_silent_passthrough():
    notes = []
    with keep_awake(False, notify=notes.append):
        pass
    assert notes == []                       # nothing announced when off


def test_enabled_yields_and_never_raises():
    # On Windows this actually calls the power API; elsewhere it warns. Either way: no exception,
    # the block runs, and (when enabled) exactly one status line is emitted.
    notes = []
    ran = False
    with keep_awake(True, notify=notes.append):
        ran = True
    assert ran
    assert len(notes) == 1                    # either "keeping awake" or the honest fallback warning
