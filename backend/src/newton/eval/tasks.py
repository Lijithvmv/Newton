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


# The REAL-PRODUCT regime: build a whole small application from scratch, spanning several modules that
# must cohere, checked by RUNNING it (not just importing). These are meant to be HARD for a 7B on the
# first pass — real business logic (atomic transactions, error semantics) and a service that must
# actually start and serve — so the effort curve has room to bend. This is where we find what's
# lacking. The checker is stdlib-only so it runs in Newton's own interpreter with no dependency install.

_LEDGER_TEST = '''\
import pytest
from service import Ledger

# Each test gets its OWN db file via pytest's tmp_path, so nothing has to delete a file another
# connection may still hold open (that would be a Windows file-lock artifact, not a logic result).


def _ledger(tmp_path):
    return Ledger(str(tmp_path / "ledger.db"))


def test_open_and_balance(tmp_path):
    lg = _ledger(tmp_path)
    a = lg.open_account("alice", 10000)
    assert lg.balance(a) == 10000


def test_transfer_moves_money_and_conserves_total(tmp_path):
    lg = _ledger(tmp_path)
    a = lg.open_account("alice", 10000)
    b = lg.open_account("bob", 0)
    lg.transfer(a, b, 3000)
    assert lg.balance(a) == 7000
    assert lg.balance(b) == 3000
    assert lg.balance(a) + lg.balance(b) == 10000


def test_overdraft_raises_and_is_atomic(tmp_path):
    lg = _ledger(tmp_path)
    a = lg.open_account("alice", 100)
    b = lg.open_account("bob", 0)
    with pytest.raises(ValueError):
        lg.transfer(a, b, 500)
    assert lg.balance(a) == 100      # a failed transfer must leave both balances untouched
    assert lg.balance(b) == 0


def test_persistence_across_reopen(tmp_path):
    path = str(tmp_path / "ledger.db")
    lg = Ledger(path)
    a = lg.open_account("alice", 5000)
    b = lg.open_account("bob", 0)
    lg.transfer(a, b, 2000)
    lg2 = Ledger(path)               # reopen the SAME sqlite file
    assert lg2.balance(a) == 3000
    assert lg2.balance(b) == 2000
'''

_URLSHORT_CHECK = '''\
import json
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def main():
    port = _free_port()
    proc = subprocess.Popen([sys.executable, "app.py", "--port", str(port)])
    base = "http://127.0.0.1:%d" % port
    try:
        up = False
        for _ in range(50):
            if proc.poll() is not None:
                print("server process exited early, code", proc.returncode)
                return 1
            try:
                urllib.request.urlopen(base + "/__ping__", timeout=1)
                up = True
                break
            except urllib.error.HTTPError:
                up = True           # any HTTP response means it is serving
                break
            except Exception:
                time.sleep(0.2)
        if not up:
            print("server did not start")
            return 1

        req = urllib.request.Request(
            base + "/shorten",
            data=json.dumps({"url": "https://example.com/page"}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        code = json.loads(urllib.request.urlopen(req, timeout=3).read().decode())["code"]
        assert code, "no code returned from /shorten"

        opener = urllib.request.build_opener(_NoRedirect)
        try:
            opener.open(base + "/" + code, timeout=3)
            print("expected a 302 redirect, got a 2xx")
            return 1
        except urllib.error.HTTPError as e:
            if e.code != 302:
                print("expected 302, got", e.code)
                return 1
            if e.headers.get("Location") != "https://example.com/page":
                print("bad Location header:", e.headers.get("Location"))
                return 1

        try:
            urllib.request.urlopen(base + "/zzznope", timeout=3)
            print("expected 404 for an unknown code")
            return 1
        except urllib.error.HTTPError as e:
            if e.code != 404:
                print("expected 404, got", e.code)
                return 1

        print("OK")
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
'''

REAL_TASKS: list[EvalTask] = [
    # A) Real business logic + persistence + atomic error semantics, across two modules.
    EvalTask(
        id="ledger",
        goal=(
            "Build a persistent bank ledger in this project using ONLY the Python standard library "
            "(sqlite3), split across two modules:\n"
            "- `db.py`: a function `connect(path)` that returns a sqlite3 connection to the database "
            "file at `path`, and `init_db(conn)` that creates the tables you need (an accounts table "
            "with an integer id, a name, and an integer balance in cents).\n"
            "- `service.py`: a class `Ledger` with:\n"
            "    * `__init__(self, path)` — open/create the sqlite database at `path` (use db.py) and "
            "initialise it;\n"
            "    * `open_account(self, name, opening_balance=0)` — create an account and return its "
            "integer id;\n"
            "    * `balance(self, account_id)` — return the account's integer balance;\n"
            "    * `transfer(self, src_id, dst_id, amount)` — move `amount` cents from src to dst. It "
            "MUST raise `ValueError` if either account does not exist OR the source has insufficient "
            "funds, and on that error it must NOT change any balance (atomic).\n"
            "All balances must persist in the sqlite file, so reopening `Ledger(path)` on the same "
            "path sees them. Do not modify the test file."),
        target="",
        files={"test_ledger.py": _LEDGER_TEST},
        check='{py} -B -m pytest -q test_ledger.py',
    ),
    # B) A service that must ACTUALLY RUN and serve HTTP — the "assembled app doesn't start" gap.
    EvalTask(
        id="urlshort",
        goal=(
            "Build a URL shortener as a RUNNABLE web service in `app.py`, using ONLY the Python "
            "standard library (http.server + sqlite3 — no third-party packages). Running "
            "`python app.py --port <PORT>` must start the server on that port. Endpoints:\n"
            "- `POST /shorten` with a JSON body {\"url\": \"<the url>\"} → respond 200 with a JSON "
            "body {\"code\": \"<short code>\"}; generate a short code and store the code→url mapping.\n"
            "- `GET /<code>` → if the code exists, respond with HTTP status 302 and a `Location` "
            "header equal to the original url; if it does not exist, respond 404.\n"
            "Persist the code→url mapping in a sqlite database file so it survives a restart. Read the "
            "port from the `--port` command-line argument. Do not modify the check file."),
        target="",
        files={"check.py": _URLSHORT_CHECK},
        check='{py} -B check.py',
    ),
]
