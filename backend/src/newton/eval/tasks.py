"""Eval tasks — small, real, and OBJECTIVELY checkable.

Each task ships its own checker (a `pytest` file or a check script), so success is a hard pass/fail
a command decides — never a model's opinion. Kept stdlib-only so the check runs in Newton's own
interpreter with no dependency install. `{py}` in `check` is substituted with the Python executable.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class EvalTask:
    id: str
    goal: str                                   # the instruction handed to both arms
    target: str                                 # the file a solution must produce/edit
    files: dict[str, str] = field(default_factory=dict)   # initial sandbox files (incl. the checker)
    check: str = '{py} -m pytest -q'            # run in the sandbox; exit 0 == the task succeeded


SEED_TASKS: list[EvalTask] = [
    # 1) Implement two functions from a spec, verified by a bundled test.
    EvalTask(
        id="implement-calc",
        goal="In calc.py, implement add(a, b) returning their sum and mul(a, b) returning their product.",
        target="calc.py",
        files={
            "test_calc.py": (
                "from calc import add, mul\n\n"
                "def test_add():\n    assert add(2, 3) == 5\n\n"
                "def test_mul():\n    assert mul(4, 5) == 20\n"
            ),
        },
    ),
    # 2) Fix a real bug so the existing test passes (an off-by-one in a mean).
    EvalTask(
        id="fix-mean-bug",
        goal="Fix the bug in stats.py so the tests pass. mean() must return the arithmetic mean.",
        target="stats.py",
        files={
            "stats.py": "def mean(xs):\n    return sum(xs) / len(xs) + 1  # bug\n",
            "test_stats.py": "from stats import mean\n\ndef test_mean():\n    assert mean([2, 4, 6]) == 4\n",
        },
    ),
    # 3) Add behaviour to an existing module without breaking it (parametrized rounding).
    EvalTask(
        id="add-rounding",
        goal=("In money.py, add a function to_cents(amount) that converts a float amount of dollars to "
              "an integer number of cents, rounding to the nearest cent (2.675 -> 268)."),
        target="money.py",
        files={
            "money.py": "def to_dollars(cents):\n    return cents / 100\n",
            "test_money.py": (
                "from money import to_cents, to_dollars\n\n"
                "def test_to_cents():\n    assert to_cents(2.675) == 268\n    assert to_cents(10.0) == 1000\n\n"
                "def test_existing_untouched():\n    assert to_dollars(250) == 2.5\n"
            ),
        },
    ),
    # 4) A CLI behaviour change, checked by running the script (a bundled check script).
    EvalTask(
        id="cli-shout-flag",
        goal=("In greet.py, add a --shout flag so that running the script with --shout prints the "
              "greeting in uppercase (HELLO) instead of lowercase (hello)."),
        target="greet.py",
        files={
            "greet.py": (
                "def main():\n    print('hello')\n\n\nif __name__ == '__main__':\n    main()\n"
            ),
            "check.py": (
                "import subprocess, sys\n"
                "plain = subprocess.run([sys.executable, 'greet.py'], capture_output=True, text=True).stdout.strip()\n"
                "shout = subprocess.run([sys.executable, 'greet.py', '--shout'], capture_output=True, text=True).stdout.strip()\n"
                "sys.exit(0 if plain == 'hello' and shout == 'HELLO' else 1)\n"
            ),
        },
        check='{py} check.py',
    ),
]


# The HARD regime: coordinated CROSS-FILE changes in an existing repo. A one-shot prompt tends to
# lose consistency between files (rename here but not there); the decomposition-loop's per-step
# focus + context propagation is meant to keep them consistent. `target` is unused (the change
# spans files); the checker (test_*.py) is restored before checking so neither arm can game it.
HARD_TASKS: list[EvalTask] = [
    # Cross-file rename + behaviour change: geometry.py AND its caller report.py must both change.
    EvalTask(
        id="xfile-rename",
        goal=("Rename the function `area_of_circle` to `circle_area` everywhere it is defined AND "
              "called, and make it use math.pi instead of the literal 3.14159. Do not modify test files."),
        target="",
        files={
            "geometry.py": "def area_of_circle(r):\n    return 3.14159 * r * r\n",
            "report.py": ("from geometry import area_of_circle\n\n"
                          "def summary(r):\n    return f'Area is {area_of_circle(r):.2f}'\n"),
            "test_geo.py": ("import math\nfrom geometry import circle_area\nfrom report import summary\n\n"
                            "def test_renamed_uses_pi():\n    assert abs(circle_area(2) - math.pi * 4) < 1e-6\n\n"
                            "def test_caller_updated():\n    assert summary(2) == f'Area is {math.pi * 4:.2f}'\n"),
        },
    ),
    # Add a field across model + logic: models.py AND service.py must both change consistently.
    EvalTask(
        id="xfile-add-field",
        goal=("Add an `email: str` field to the User dataclass in models.py, and change greet() in "
              "service.py to include the email in parentheses, like 'Hello NAME (EMAIL)'. "
              "Do not modify test files."),
        target="",
        files={
            "models.py": "from dataclasses import dataclass\n\n@dataclass\nclass User:\n    name: str\n",
            "service.py": "from models import User\n\ndef greet(u):\n    return f'Hello {u.name}'\n",
            "test_user.py": ("from models import User\nfrom service import greet\n\n"
                             "def test_email_field_and_greeting():\n"
                             "    u = User(name='A', email='a@x.com')\n"
                             "    assert greet(u) == 'Hello A (a@x.com)'\n"),
        },
    ),
]


def _large_repo_files() -> dict[str, str]:
    """A repo where the needed constant (SURCHARGE = 37) is buried in ONE noise-named file (mod07.py)
    — an UNGUESSABLE location AND name. A solution can't pass by guessing the import; it must actually
    RETRIEVE the right file. This is what forces the context-engine to earn its keep."""
    files = {
        "core/money.py": "def total(amount):\n    return amount\n",
        "test_money.py": ("from core.money import total\n\n"
                          "def test_total_adds_surcharge():\n    assert total(100) == 137\n"),
    }
    for i in range(14):
        extra = "\nSURCHARGE = 37\n" if i == 7 else ""    # the needle among the noise
        files[f"mod{i:02d}.py"] = f"def helper_{i}(x):\n    return x * {i} + {i}\n\nCONST_{i} = {i * 7}\n{extra}"
    return files


# The LARGE regime: the needed symbol is buried among many files under an unguessable name — only a
# context-engine that RETRIEVES the right file (not just the target) can solve it.
LARGE_TASKS: list[EvalTask] = [
    EvalTask(
        id="large-find-surcharge",
        goal=("In core/money.py, change total(amount) so it returns amount plus the SURCHARGE "
              "constant that is already defined somewhere in this project. Find where it is defined, "
              "import it correctly, and use it. Do not change any test file."),
        target="",
        files=_large_repo_files(),
    ),
]
