"""project_tree is what the model sees of the project: Newton's own runtime state must never be in it."""

from __future__ import annotations

from newton.context import project_tree


def test_newton_state_is_not_shown_to_the_model(tmp_path):
    (tmp_path / "greet.py").write_text("print('hello')\n", encoding="utf-8")
    state = tmp_path / ".newton"
    (state / "baseline").mkdir(parents=True)
    (state / "loop.json").write_text("{}", encoding="utf-8")
    (state / "baseline" / "greet.py").write_text("print('hello')\n", encoding="utf-8")
    tree = project_tree(tmp_path)
    assert "greet.py" in tree
    assert ".newton" not in tree and "loop.json" not in tree and "baseline" not in tree


def test_newton_state_cannot_push_real_files_past_the_cap(tmp_path):
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    state = tmp_path / ".newton"
    state.mkdir()
    for i in range(80):                                  # more state files than the 60-entry cap
        (state / f"run{i:02d}.json").write_text("{}", encoding="utf-8")
    assert "app.py" in project_tree(tmp_path, max_entries=60)
