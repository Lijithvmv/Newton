"""Advisory Laya judge - the eval-first placement of a local, calibrated decision model.

The Jev-loop blueprint's staged adoption puts a decision model in the EVALUATION role FIRST, because
evaluation is the one placement where the model's answer does not change what the agent does. So this
runs Laya as an ADVISORY judge: at the end of a run it scores each check's output with the same
output-failure Noul the spike measured, records its answer as a Decision (decider="laya") ALONGSIDE
the deterministic ground truth, and appends the (ground_truth, prediction, confidence) pair to an
advisory log. It never gates, blocks, or changes a single loop action.

Over real runs that log becomes a real labeled corpus - Newton's OWN outputs, labeled by the
deterministic result - to measure Laya's agreement and drift, and later to calibrate it before it is
ever trusted to act. Optional + lazy: Laya (torch/transformers, ~808MB) loads only when advisory
judging is switched on (NEWTON_LOOP_ADVISORY_JUDGE=1) and is installed; Newton runs identically
without it.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from .decision import NOUL, Decision

_FAILURE_Q = (
    "Does this command, build, or test output indicate that it FAILED - an error, a traceback, a "
    "crash, or failing tests - as opposed to succeeding (tests passing, a clean build, a warning "
    "that is not fatal)?"
)


# Laya is PINNED to one exact revision. An unpinned `laya.load("convaiinnovations/laya")` re-resolves
# "main" on the Hugging Face Hub on every load: a network call carrying your IP + the model name, and
# weights that can change underneath a measured baseline (the cache already held two different
# snapshots). Resolving a fixed revision from the local cache means repeat loads touch NO network and
# always use the weights the validation measured. Bump deliberately, then re-run the validation.
LAYA_REPO = "convaiinnovations/laya"
LAYA_REVISION = os.getenv("NEWTON_LAYA_REVISION", "5e7b2b1b8ca2ecdd3f2322d94069c9b6ce7e844b")
_LAYA_FILES = ["encoder/*", "model.safetensors", "rl_agent_config.json", "tokenizer/*"]


def resolve_laya_path(model: str = LAYA_REPO, revision: str = LAYA_REVISION) -> str:
    """Local directory of the pinned Laya checkpoint. Cached -> resolved fully offline (no Hub call).
    Not cached yet (first run on this machine) -> fetch exactly that revision's files, once. A local
    directory passed as `model` is used as-is."""
    if os.path.isdir(model):
        return model
    from huggingface_hub import snapshot_download
    try:
        return snapshot_download(model, revision=revision, allow_patterns=_LAYA_FILES,
                                 local_files_only=True)
    except Exception:
        return snapshot_download(model, revision=revision, allow_patterns=_LAYA_FILES)


class LayaJudge:
    """A process-wide Laya judge that loads the model in the BACKGROUND and never blocks a run.

    The model load is heavy (tens of seconds), so it must not sit in the loop's critical path - an
    advisory judge that delays the user's result is a broken advisory judge. So the first construction
    kicks a background thread to load the model; until it's ready, `judge_failure` returns None and the
    run proceeds untouched. Once loaded, the cached agent scores every later check in ~sub-second. Every
    path fails soft: not-installed, still-loading, or a call error all return None."""

    _lock = threading.Lock()
    _agent: Any = None
    _unavailable: str | None = None
    _loading = False

    def __init__(self, model: str = "convaiinnovations/laya") -> None:
        self.model = model
        self.start_loading()

    def start_loading(self) -> None:
        """Kick a one-time background load if it hasn't started. Non-blocking."""
        with LayaJudge._lock:
            if (LayaJudge._agent is not None or LayaJudge._unavailable is not None
                    or LayaJudge._loading):
                return
            LayaJudge._loading = True
        threading.Thread(target=self._load, name="laya-load", daemon=True).start()

    def _load(self) -> None:
        try:
            # Load-time fixes (measured: >180s hang -> ~16s CPU). The real one is laya >= 0.3.7 —
            # < 0.3.7 deadlocks on newer Windows Python. These flags are cheap, safe defaults an
            # operator can override: skip the TF runtime probe (we have no TensorFlow), and disable
            # oneDNN. The checkpoint is resolved from a PINNED revision (resolve_laya_path), so repeat
            # loads never contact the Hub; only a machine's very first load fetches that exact revision.
            os.environ.setdefault("USE_TF", "0")
            os.environ.setdefault("TF_ENABLE_ONEDNN_OPTS", "0")
            import laya  # type: ignore   (needs laya >= 0.3.7; 0.3.10+ recommended)
            agent = laya.load(resolve_laya_path(self.model))
            with LayaJudge._lock:
                LayaJudge._agent = agent
        except Exception as e:                        # ImportError or a load/runtime failure
            with LayaJudge._lock:
                LayaJudge._unavailable = str(e)
        finally:
            with LayaJudge._lock:
                LayaJudge._loading = False

    @property
    def available(self) -> bool:
        """True only once the background load has finished successfully — never triggers a blocking
        load, so it's safe to poll in the hot path."""
        return LayaJudge._agent is not None

    def judge_failure(self, text: str) -> Decision | None:
        """Score one check output as a failure-or-not Noul. Returns a Laya `Decision`, or None when
        the model isn't loaded yet (still loading / not installed) or the call fails - never blocks."""
        if not text or not self.available:
            return None
        try:
            res = LayaJudge._agent.predict(
                text, {"failed": {"type": "noul", "instructions": _FAILURE_Q}})
            ans = res["answers"]["failed"]
            p = float(ans["noul"])
            conf = float(ans.get("confidence", max(p, 1.0 - p)))
        except Exception:
            return None
        return Decision(name="advisory_output_check", kind=NOUL,
                        question="Is this check output a failure?",
                        answer="failure" if p >= 0.5 else "ok",
                        confidence=conf, reason=f"P(failure)={p:.3f}", decider="laya")


def append_advisory(path: Path, row: dict[str, Any]) -> None:
    """Append one (ground_truth, prediction, confidence) record to the advisory JSONL log. Best-effort
    - a logging failure must never break a run."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({**row, "at": time.time()}) + "\n")
    except OSError:
        pass


def read_advisory(path: Path) -> list[dict]:
    """All advisory records, oldest first. Missing/corrupt lines are skipped, never fatal."""
    rows: list[dict] = []
    try:
        for ln in path.read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if ln:
                try:
                    rows.append(json.loads(ln))
                except ValueError:
                    continue
    except OSError:
        return []
    return rows
