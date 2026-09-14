"""Keep the machine awake for the duration of a long autonomous run.

Newton's edge is spending unlimited local time — but if the laptop sleeps mid-build, the process is
frozen and makes zero progress until it wakes. This asks the OS to keep the system awake while a run
is in flight, then releases the request so normal power management resumes the moment the run ends.

Windows (Newton's target box) via `SetThreadExecutionState`; on other platforms it is an honest no-op
with a one-line warning rather than a silent lie. The display is allowed to sleep — only *system*
sleep is held off, which is what a headless overnight build needs.
"""

from __future__ import annotations

import contextlib
import sys
from collections.abc import Callable, Iterator

# Windows SetThreadExecutionState flags.
_ES_CONTINUOUS = 0x80000000        # this state stays in effect until the next call
_ES_SYSTEM_REQUIRED = 0x00000001   # reset the system idle timer → do not sleep


@contextlib.contextmanager
def keep_awake(enabled: bool = True, *, notify: Callable[[str], None] = lambda _m: None) -> Iterator[None]:
    """While the block runs, keep the system from sleeping (best-effort). `enabled=False` is a plain
    pass-through. Never raises — a power API that isn't available must not break a build."""
    if not enabled:
        yield
        return
    if not sys.platform.startswith("win"):
        notify("keep-awake is only implemented on Windows; the run continues without it.")
        yield
        return
    import ctypes

    kernel32 = getattr(ctypes, "windll", None)
    if kernel32 is None:
        yield
        return
    try:
        kernel32.kernel32.SetThreadExecutionState(_ES_CONTINUOUS | _ES_SYSTEM_REQUIRED)
        notify("Keeping the machine awake for this run (system sleep held off).")
    except Exception:
        notify("Could not enable keep-awake; the run may pause if the machine sleeps.")
        yield
        return
    try:
        yield
    finally:
        with contextlib.suppress(Exception):
            kernel32.kernel32.SetThreadExecutionState(_ES_CONTINUOUS)   # release the hold
