"""NEWTON_LOOP_NO_TEST_IMPORTS: production code never imports a test module.

Reproduces the 2026-10-19 trace exactly: gemma3:4b wrote `from test_calc import test_add, test_mul`
atop a correct calc.py, making a circular import with test_calc.py that failed at line 1."""

from __future__ import annotations

import subprocess
import sys

from newton.config import load_settings
from newton.loop.engine import LoopEngine, _is_test_module, _unused_test_imports

_CALC = ("from test_calc import test_add, test_mul\n\n"
         "def add(a, b):\n    return a + b\n\ndef mul(a, b):\n    return a * b\n")
_TEST = ("from calc import add, mul\n\n"
         "def test_add():\n    assert add(2, 3) == 5\n\n"
         "def test_mul():\n    assert mul(4, 5) == 20\n")


def _engine(tmp_path):
    return LoopEngine(load_settings(tmp_path))


def _imports_cleanly(tmp_path) -> bool:
    r = subprocess.run([sys.executable, "-B", "-c", "import calc"], cwd=tmp_path, capture_output=True)
    return r.returncode == 0


def test_the_measured_circular_import_is_repaired(tmp_path, monkeypatch):
    monkeypatch.setenv("NEWTON_LOOP_NO_TEST_IMPORTS", "1")
    (tmp_path / "calc.py").write_text(_CALC, encoding="utf-8")
    (tmp_path / "test_calc.py").write_text(_TEST, encoding="utf-8")
    assert not _imports_cleanly(tmp_path)                       # the failure as measured
    assert _engine(tmp_path)._repair_imports("calc.py")
    assert "test_calc" not in (tmp_path / "calc.py").read_text(encoding="utf-8")
    assert _imports_cleanly(tmp_path)
    assert (tmp_path / "test_calc.py").read_text(encoding="utf-8") == _TEST   # test file untouched


def test_off_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("NEWTON_LOOP_NO_TEST_IMPORTS", raising=False)
    (tmp_path / "calc.py").write_text(_CALC, encoding="utf-8")
    (tmp_path / "test_calc.py").write_text(_TEST, encoding="utf-8")
    _engine(tmp_path)._repair_imports("calc.py")
    assert (tmp_path / "calc.py").read_text(encoding="utf-8") == _CALC


def test_a_used_test_import_is_kept():
    import ast
    tree = ast.parse("from test_helpers import make_case\n\ndef build():\n    return make_case()\n")
    assert _unused_test_imports(tree) == set()


def test_plain_and_multiline_test_imports_are_found():
    import ast
    tree = ast.parse("import test_calc\nfrom tests.test_x import (\n    a,\n    b,\n)\nimport os\n\nx = os.sep\n")
    assert _unused_test_imports(tree) == {1, 2, 3, 4, 5}       # import os (line 6) is kept


def test_test_files_are_never_rewritten(tmp_path, monkeypatch):
    monkeypatch.setenv("NEWTON_LOOP_NO_TEST_IMPORTS", "1")
    src = "from test_other import helper\n\ndef test_a():\n    assert True\n"
    (tmp_path / "test_a.py").write_text(src, encoding="utf-8")
    _engine(tmp_path)._repair_imports("test_a.py")
    assert (tmp_path / "test_a.py").read_text(encoding="utf-8") == src


def test_is_test_module():
    assert _is_test_module("test_calc") and _is_test_module("pkg/test_calc.py") and _is_test_module("pkg.calc_test")
    assert _is_test_module("conftest") and not _is_test_module("calc") and not _is_test_module("contest")
