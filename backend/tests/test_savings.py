"""Cloud-cost-avoided meter — pricing math, honest token fallback, and a guarded, no-op-until-
configured ledger. The meter must never affect the work, so 'unconfigured = silent no-op' is a test.
"""

from __future__ import annotations

from newton import savings


class _Usage:
    def __init__(self, p, c):
        self.prompt_tokens, self.completion_tokens = p, c


class _Resp:
    """Minimal OpenAI-shaped response."""
    def __init__(self, content="hi", usage=None):
        self.usage = usage
        self.choices = [type("C", (), {"message": type("M", (), {"content": content})()})()]


# --- pricing ---

def test_saved_usd_prices_against_baseline():
    # 1M input + 1M output at claude-sonnet (3 / 15 per Mtok) = $18.
    assert savings.saved_usd(1_000_000, 1_000_000, "claude-sonnet") == 18.0
    assert savings.saved_usd(1_000_000, 0, "gpt-4o") == 2.5

def test_unknown_baseline_falls_back_to_default():
    assert savings.saved_usd(1_000_000, 0, "no-such-model") == \
        savings.saved_usd(1_000_000, 0, savings.DEFAULT_BASELINE)


# --- token accounting: prefer reported usage, else estimate and flag it ---

def test_usage_prefers_reported_counts():
    pt, ct, est = savings.usage_from_response(_Resp(usage=_Usage(100, 50)), [{"content": "x"}])
    assert (pt, ct, est) == (100, 50, False)

def test_usage_estimates_from_bytes_when_absent():
    msgs = [{"content": "a" * 40}]
    pt, ct, est = savings.usage_from_response(_Resp("hello world!", usage=None), msgs)
    assert est is True and pt == 10 and ct == len("hello world!") // 4   # ~4 chars/token


# --- ledger: accumulation, persistence, and no-op until configured ---

def test_record_and_summary_accumulate(tmp_path):
    savings.configure(tmp_path)
    savings.record(100, 50)
    savings.record(100, 50)
    s = savings.summary()
    assert s["calls"] == 2 and s["input_tokens"] == 200 and s["output_tokens"] == 100
    assert s["estimated"] is False
    assert s["saved_usd"] == round(savings.saved_usd(200, 100), 4)

def test_record_response_end_to_end(tmp_path):
    savings.configure(tmp_path)
    savings.record_response(_Resp(usage=_Usage(1_000_000, 0)), [{"content": "x"}])
    assert savings.summary()["saved_usd"] == savings.saved_usd(1_000_000, 0)

def test_estimated_calls_flag_the_summary(tmp_path):
    savings.configure(tmp_path)
    savings.record_response(_Resp("some reply text", usage=None), [{"content": "the prompt"}])
    s = savings.summary()
    assert s["estimated"] is True and "estimate" in s["note"].lower()

def test_ledger_persists_to_disk(tmp_path):
    savings.configure(tmp_path)
    savings.record(1000, 500)
    assert (tmp_path / ".newton" / "savings.json").is_file()
    savings.configure(tmp_path)                       # "reload" — reads the same file
    assert savings.summary()["input_tokens"] == 1000

def test_no_op_when_unconfigured(monkeypatch):
    monkeypatch.setattr("newton.savings._ledger_path", None)
    savings.record(999, 999)                          # must not raise, must not write
    savings.record_response(_Resp(usage=_Usage(5, 5)), [{"content": "x"}])
    assert savings.summary()["calls"] == 0            # nothing recorded
