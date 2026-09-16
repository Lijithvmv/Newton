"""The cheap output validator (from toolgrad) — must flag unambiguous failures that exit 0, and must
NOT flag legitimate output (false positives would send good steps into needless repair)."""

from __future__ import annotations

from newton.loop.output_check import error_shaped


def test_flags_real_failure_signatures():
    assert error_shaped("Traceback (most recent call last):\n  File \"x.py\", line 1")
    assert error_shaped("ModuleNotFoundError: No module named 'foo'")
    assert error_shaped("bash: widget: command not found")
    assert error_shaped("cat: nope.txt: No such file or directory")
    assert error_shaped("'foo' is not recognized as an internal or external command")
    assert error_shaped("<!DOCTYPE html><html><body>500 Internal Server Error</body></html>")


def test_does_not_flag_legitimate_output():
    assert not error_shaped("")
    assert not error_shaped("   \n  ")
    assert not error_shaped("3 passed in 0.21s")
    assert not error_shaped("wrote calc.py (128 chars)")
    assert not error_shaped("Hello, world!")
    assert not error_shaped("No errors found. All checks passed.")   # generic 'error' must not trip
    assert not error_shaped("Build succeeded: 42 files compiled")
