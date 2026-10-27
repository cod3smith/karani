"""`karani watch` — argument surface and the unconfigured path.

The verb has to be usable before Gmail exists: a user who has not done
the Google Cloud setup should get told what to do, not a traceback.
"""
from __future__ import annotations

import pytest

from karani import cli


def test_the_watcher_section_carries_no_credentials():
    """[watcher] says how much mail to read, never how to get at it —
    GMAIL_* stays in env like every other secret (CLAUDE.md 4.8)."""
    from karani.config.schema import WatcherCfg

    fields = set(WatcherCfg.model_fields)
    assert fields == {"lookback_days", "max_messages", "query"}
    assert WatcherCfg().lookback_days == 14


def test_watch_help_lists_the_flags(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["karani", "watch", "--help"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for flag in ("--days", "--limit", "--no-slack", "--authorize"):
        assert flag in out
    assert "read-only" in out


def test_missing_credentials_explain_the_setup(monkeypatch, capsys):
    """No GMAIL_* vars: a pointer to the setup, exit 2, no traceback."""
    for var in ("GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET",
                "GMAIL_REFRESH_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr("sys.argv", ["karani", "watch", "--no-slack"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "GMAIL_CLIENT_ID" in err
    assert "docs/watcher.md" in err


def test_authorize_without_the_extra_says_which_extra(monkeypatch, capsys):
    monkeypatch.setenv("GMAIL_CLIENT_ID", "cid")
    monkeypatch.setenv("GMAIL_CLIENT_SECRET", "secret")
    import karani.watcher.gmail as gmail_mod

    def no_sdk(*args, **kwargs):
        raise gmail_mod.GmailAuthError(
            "The authorization flow needs the `gmail` extra: "
            "uv sync --extra gmail (or pip install 'karani[gmail]').")

    monkeypatch.setattr(gmail_mod, "authorize", no_sdk)
    monkeypatch.setattr("sys.argv", ["karani", "watch", "--authorize"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    assert "gmail" in capsys.readouterr().err
