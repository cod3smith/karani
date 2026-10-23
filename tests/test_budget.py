"""Spend accounting and the budget guard (ADR 0019).

karani shares one Anthropic account with the user's other projects, so
it gets a small allocation rather than the whole balance. The guard has
to degrade the way everything else in karani degrades: fall back to the
free local model and say so, never crash a pass and never quietly keep
spending.
"""
from __future__ import annotations

import pytest

from karani import budget
from karani.qualification import usage


@pytest.fixture(autouse=True)
def _clean():
    budget.reset()
    usage.reset()
    yield
    budget.reset()
    usage.reset()


# --- pricing ---

def test_prices_the_current_models():
    # 1M input + 1M output on Opus 5.5 = $4 + $20
    cost = budget.cost_usd({"model": "claude-opus-5-5",
                            "prompt_tokens": 1_000_000,
                            "completion_tokens": 1_000_000})
    assert cost == pytest.approx(24.0)
    # Haiku 5.5 is the bulk tier: same volume, two orders cheaper
    assert budget.cost_usd({"model": "claude-haiku-5-5",
                            "prompt_tokens": 1_000_000,
                            "completion_tokens": 1_000_000}) == pytest.approx(0.6)


def test_cache_reads_are_discounted_not_free():
    """A cached token is billed at a fraction of input — pricing it at
    zero would make the guard think caching is free money."""
    plain = budget.cost_usd({"model": "claude-haiku-5-5",
                             "prompt_tokens": 1_000_000})
    cached = budget.cost_usd({"model": "claude-haiku-5-5",
                              "prompt_tokens": 1_000_000,
                              "cache_read_tokens": 1_000_000})
    assert 0 < cached < plain
    # Writes cost more than plain input (1.25x), which is why a prefix
    # below the model minimum is worth avoiding.
    written = budget.cost_usd({"model": "claude-haiku-5-5",
                               "prompt_tokens": 1_000_000,
                               "cache_write_tokens": 1_000_000})
    assert written > plain


def test_local_and_unknown_models_cost_nothing():
    assert budget.cost_usd({"model": "qwen3:4b",
                            "prompt_tokens": 9_000_000}) == 0.0
    assert budget.cost_usd({}) == 0.0


# --- the running total ---

def test_spend_tracks_calls_made_during_the_pass():
    budget.set_month_to_date(2.0)
    usage.record({"model": "claude-opus-5-5", "prompt_tokens": 1_000_000,
                  "completion_tokens": 0})
    assert budget.spent() == pytest.approx(6.0)   # 2.00 ledger + 4.00 now


def test_cap_comes_from_config(tmp_path):
    from karani.config import reload_config
    cfg = tmp_path / "karani.toml"
    cfg.write_text("version = 1\n[budget]\nmonthly_usd = 3.5\n")
    reload_config(cfg)
    try:
        assert budget.cap() == pytest.approx(3.5)
    finally:
        reload_config()


def test_allows_hosted_until_the_cap(tmp_path):
    from karani.config import reload_config
    cfg = tmp_path / "karani.toml"
    cfg.write_text("version = 1\n[budget]\nmonthly_usd = 5\n")
    reload_config(cfg)
    try:
        budget.set_month_to_date(4.0)
        assert budget.allows_hosted() is True
        budget.set_month_to_date(5.5)
        assert budget.allows_hosted() is False
    finally:
        reload_config()


# --- the factory gate ---

def _local_cfg(tmp_path, monkeypatch, cap="5"):
    from karani.config import reload_config
    cfg = tmp_path / "karani.toml"
    cfg.write_text(f'''version = 1
[budget]
monthly_usd = {cap}
[llm.qualify]
provider = "anthropic"
model = "claude-haiku-5-5"
''')
    reload_config(cfg)
    return cfg


def test_over_cap_falls_back_to_local(tmp_path, monkeypatch):
    from karani.config import reload_config
    from karani.qualification.factory import get_qualifier
    from karani.qualification.local import LocalQualifier
    _local_cfg(tmp_path, monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    try:
        budget.set_month_to_date(99.0)
        client = get_qualifier(task="qualify")
        assert isinstance(client, LocalQualifier), "must keep hunting, free"
        assert "budget" in budget.degraded().lower()
    finally:
        reload_config()


def test_missing_key_falls_back_instead_of_crashing(tmp_path, monkeypatch):
    """Config can name a hosted provider before the key exists — that
    must not turn every hourly pass into a hard failure."""
    from karani.config import reload_config
    from karani.qualification.factory import get_qualifier
    from karani.qualification.local import LocalQualifier
    _local_cfg(tmp_path, monkeypatch)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    try:
        client = get_qualifier(task="qualify")
        assert isinstance(client, LocalQualifier)
        assert "key" in budget.degraded().lower()
    finally:
        reload_config()


def test_under_cap_with_a_key_uses_the_hosted_provider(tmp_path, monkeypatch):
    pytest.importorskip("anthropic")
    from karani.config import reload_config
    from karani.qualification.anthropic import AnthropicQualifier
    from karani.qualification.factory import get_qualifier
    _local_cfg(tmp_path, monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    try:
        budget.set_month_to_date(0.10)
        client = get_qualifier(task="qualify")
        assert isinstance(client, AnthropicQualifier)
        assert client.model_name == "claude-haiku-5-5"
        assert budget.degraded() == ""
    finally:
        reload_config()


# --- announcements ---

def test_degradation_announces_once_not_hourly():
    """A spent budget stays spent for weeks. Alerting every pass would
    train the channel to ignore karani."""
    budget.note_degraded("budget spent")
    first = budget.pending_announcement()
    assert first and "budget spent" in first
    assert budget.pending_announcement() is None      # silent thereafter


def test_recovery_is_announced_too():
    budget.note_degraded("budget spent")
    budget.pending_announcement()
    budget.note_degraded("")                           # new month / key added
    msg = budget.pending_announcement()
    assert msg and "restored" in msg.lower()
    assert budget.pending_announcement() is None
