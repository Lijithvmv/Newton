"""Cloud-cost-avoided meter — put a number on Newton's zero-token-cost edge.

Newton runs entirely on local inference, so every token it processes is free. This module answers the
question that makes that concrete: *what would this work have cost on a cloud API?* It taps the single
LLM chokepoint (`llm.complete`), tallies the tokens Newton processed, and prices them against a
**static, offline** snapshot of cloud rates — no network call, ever (Newton is offline-first; a live
price catalog would betray that). The running total is the local edge, in dollars.

Two honesty rules baked in:
  * the rate table is a hand-maintained snapshot, clearly labelled — not a live quote;
  * when the local runtime doesn't report token counts (Ollama usually doesn't), tokens are ESTIMATED
    from byte length (~4 chars/token) and the summary says so. A meter that overclaims is worse than
    none, so the estimate is flagged rather than hidden.

The meter must never affect the work: every entry point is guarded so a ledger error can't break a
completion, and if it was never `configure()`d (tests, eval sandboxes) it silently no-ops.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Static snapshot of representative cloud list prices, USD per 1,000,000 tokens (input, output).
# A hand-maintained reference — edit freely; NOT fetched live. Rough early-2026 published rates.
CLOUD_RATES: dict[str, tuple[float, float]] = {
    "claude-sonnet": (3.0, 15.0),
    "claude-opus": (15.0, 75.0),
    "gpt-4o": (2.5, 10.0),
    "gemini-1.5-pro": (1.25, 5.0),
}
# The baseline the "saved" figure is quoted against (a common mid-tier cloud model). Configurable.
DEFAULT_BASELINE = os.environ.get("NEWTON_SAVINGS_BASELINE", "claude-sonnet")

_ledger_path: Path | None = None


def configure(project_root: str | Path) -> None:
    """Point the meter at a project's ledger (`<root>/.newton/savings.json`). Called once at app
    startup; until then every record is a no-op, so importing this module changes nothing."""
    global _ledger_path
    _ledger_path = Path(project_root) / ".newton" / "savings.json"


def _est_tokens(text: str) -> int:
    """Byte-length token estimate (~4 chars/token) — the fallback when usage isn't reported."""
    return max(0, len(text) // 4)


def usage_from_response(resp: Any, messages: list[dict[str, Any]]) -> tuple[int, int, bool]:
    """(input_tokens, output_tokens, estimated). Prefer the provider's reported `usage`; when it's
    absent (typical for local Ollama), estimate from byte length and flag it."""
    usage = getattr(resp, "usage", None)
    pt = getattr(usage, "prompt_tokens", None) if usage is not None else None
    ct = getattr(usage, "completion_tokens", None) if usage is not None else None
    if isinstance(pt, int) and isinstance(ct, int) and (pt or ct):
        return pt, ct, False
    # Fallback: estimate. Prompt = all message contents; completion = the reply text.
    prompt_chars = sum(len(str(m.get("content", ""))) for m in messages)
    try:
        reply = str(resp.choices[0].message.content or "")
    except Exception:
        reply = ""
    return max(0, prompt_chars // 4), _est_tokens(reply), True


def _load() -> dict[str, Any]:
    if _ledger_path is None or not _ledger_path.is_file():
        return {"calls": 0, "input_tokens": 0, "output_tokens": 0, "estimated_calls": 0}
    try:
        d = json.loads(_ledger_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"calls": 0, "input_tokens": 0, "output_tokens": 0, "estimated_calls": 0}
    for k in ("calls", "input_tokens", "output_tokens", "estimated_calls"):
        d.setdefault(k, 0)
    return d


def _save(d: dict[str, Any]) -> None:
    if _ledger_path is None:
        return
    _ledger_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _ledger_path.with_suffix(".json.tmp")
    d["updated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    tmp.write_text(json.dumps(d, indent=2), encoding="utf-8")
    tmp.replace(_ledger_path)


def record(input_tokens: int, output_tokens: int, *, estimated: bool = False) -> None:
    """Add one call's token counts to the ledger. No-op until configured; never raises."""
    if _ledger_path is None:
        return
    try:
        d = _load()
        d["calls"] += 1
        d["input_tokens"] += max(0, int(input_tokens))
        d["output_tokens"] += max(0, int(output_tokens))
        if estimated:
            d["estimated_calls"] += 1
        _save(d)
    except Exception:
        return                      # the meter must never break the work


def record_response(resp: Any, messages: list[dict[str, Any]]) -> None:
    """Instrumentation hook for `llm.complete`. Extracts usage and records it. Fully guarded:
    no-op if unconfigured, and it swallows every error so a completion can never fail on its account."""
    if _ledger_path is None:
        return
    try:
        pt, ct, est = usage_from_response(resp, messages)
        record(pt, ct, estimated=est)
    except Exception:
        return


def saved_usd(input_tokens: int, output_tokens: int, baseline: str | None = None) -> float:
    """Price token counts against the baseline cloud model's rates."""
    in_rate, out_rate = CLOUD_RATES.get(baseline or DEFAULT_BASELINE, CLOUD_RATES[DEFAULT_BASELINE])
    return input_tokens / 1_000_000 * in_rate + output_tokens / 1_000_000 * out_rate


def summary(baseline: str | None = None) -> dict[str, Any]:
    """The meter's public read: totals, the baseline used, dollars saved, and honesty flags."""
    baseline = baseline or DEFAULT_BASELINE
    d = _load()
    in_rate, out_rate = CLOUD_RATES.get(baseline, CLOUD_RATES[DEFAULT_BASELINE])
    dollars = round(saved_usd(d["input_tokens"], d["output_tokens"], baseline), 4)
    return {
        "calls": d["calls"],
        "input_tokens": d["input_tokens"],
        "output_tokens": d["output_tokens"],
        "baseline_model": baseline,
        "baseline_rates_usd_per_mtok": {"input": in_rate, "output": out_rate},
        "saved_usd": dollars,
        "estimated": d["estimated_calls"] > 0,
        "note": ("Offline static rate snapshot vs local (free) inference. "
                 + ("Token counts are byte-length estimates (local runtime didn't report usage)."
                    if d["estimated_calls"] else "Token counts reported by the provider.")),
    }
