"""RUN-step command normalisation: a bare `python`/`pytest` the weak model emits is mapped to the `{py}`
interpreter convention, so a correct build isn't failed by 'not recognized' on a PATH without pytest."""

from __future__ import annotations

from newton.loop.execute import _normalize_command


def test_bare_pytest_becomes_py_module():
    assert _normalize_command("pytest tests -q") == "{py} -m pytest tests -q"


def test_bare_python_and_python3_become_py():
    assert _normalize_command("python app.py --port 8000") == "{py} app.py --port 8000"
    assert _normalize_command("python3 -m pytest") == "{py} -m pytest"


def test_existing_convention_and_other_tools_untouched():
    assert _normalize_command("{py} -m pytest") == "{py} -m pytest"
    assert _normalize_command("npm run build") == "npm run build"
    assert _normalize_command('"C:/Py/python.exe" app.py') == '"C:/Py/python.exe" app.py'
    assert _normalize_command("pythonic-tool --x") == "pythonic-tool --x"   # word boundary, not a prefix
