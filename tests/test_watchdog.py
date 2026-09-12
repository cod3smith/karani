"""Venv-independent dead-man's switch.

karani was dead for 8 days and nothing said so. The existing heartbeat
(ADR 0016) runs inside `karani notify` from the daily launchd agent —
which executes the same `.venv/bin/karani` as the hourly hunt. A disk
cleanup deleted that venv, so watcher and watched died together: a
common-mode failure no in-process check can survive.

The watchdog therefore shares nothing with karani's runtime. A pass
leaves a timestamp file; a plain shell launchd job reads that file and
curls Slack. No Python, no venv, no database.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("langgraph")

from karani.orchestration.graph import (  # noqa: E402
    mark_pass_complete, pass_marker_path,
)


def test_marker_path_follows_config_override(monkeypatch, tmp_path):
    monkeypatch.setenv("KARANI_PASS_MARKER", str(tmp_path / "custom-marker"))
    assert pass_marker_path() == tmp_path / "custom-marker"


def test_mark_pass_complete_writes_timestamp(monkeypatch, tmp_path):
    marker = tmp_path / ".last-pass"
    monkeypatch.setenv("KARANI_PASS_MARKER", str(marker))
    mark_pass_complete()
    assert marker.exists()
    assert marker.read_text().startswith("20")     # ISO timestamp


def test_mark_pass_complete_never_raises(monkeypatch):
    """Bookkeeping must never sink a hunt — same rule as the ledger."""
    monkeypatch.setenv("KARANI_PASS_MARKER", "/nonexistent-dir/deep/marker")
    mark_pass_complete()                            # must not raise


@pytest.mark.asyncio
async def test_hunt_pass_leaves_a_marker(monkeypatch, tmp_path):
    """The marker must be written by the real scheduler entry point —
    a marker only touched by tests would be a lie to the watchdog."""
    import karani.ingestion.orchestrator as ing
    import karani.ingestion.resume as resume_mod
    import karani.orchestration.graph as graph_mod
    import karani.qualification as qual
    from karani.ingestion.storage import Storage

    marker = tmp_path / ".last-pass"
    monkeypatch.setenv("KARANI_PASS_MARKER", str(marker))
    monkeypatch.setattr(
        resume_mod.ResumeProfile, "from_file",
        classmethod(lambda cls, path=None: cls(raw_markdown="# K")))

    class FakeIngest:
        fetched, inserted, passed_prefilter = 4, 2, 1
        per_source: dict = {}

    class FakeQualify:
        fetched = qualified = maybe = skipped = 0
        errors: list = []
        lock_skipped = False

    async def fake_ingest(storage, profile=None):
        return FakeIngest()

    async def fake_qualify(storage, client, resume, **kw):
        return FakeQualify()

    monkeypatch.setattr(ing, "run", fake_ingest)
    monkeypatch.setattr(qual, "qualify_pending", fake_qualify)
    assert Storage  # imported for the in-memory fallback path

    await graph_mod.run_hunt_once()
    assert marker.exists(), "a completed pass must refresh the marker"


# --- the shell watchdog itself ---

def _watchdog_source() -> str:
    from importlib import resources
    return (resources.files("karani") / "resources"
            / "watchdog.sh").read_text()


def test_watchdog_is_shipped_and_self_contained():
    src = _watchdog_source()
    # Comments may say "karani"; executable lines must never invoke it,
    # a venv, or a Python — those are exactly what a cleanup can delete.
    code = "\n".join(ln for ln in src.splitlines()
                     if ln.strip() and not ln.strip().startswith("#"))
    for forbidden in (".venv", "uv run", "python"):
        assert forbidden not in code, f"watchdog must not depend on {forbidden}"
    assert not any(ln.strip().startswith("karani") for ln in code.splitlines())
    assert "chat.postMessage" in src        # alerts over plain curl
    assert "__MARKER__" in src and "__MAX_AGE_H__" in src


def test_schedule_installs_the_watchdog(monkeypatch, tmp_path):
    """`karani schedule` must install the watchdog alongside the agents —
    a dead-man's switch nobody installs protects nobody."""
    import subprocess

    import karani.cli as cli

    home = tmp_path / "home"
    (home / "Library" / "LaunchAgents").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **kw: subprocess.CompletedProcess(a, 0))
    monkeypatch.chdir(tmp_path)

    assert cli._schedule() == 0

    agents = home / "Library" / "LaunchAgents"
    plist = agents / "com.karani.watchdog.plist"
    assert plist.exists(), "watchdog plist not installed"
    script = Path(os.path.expanduser("~/.karani/watchdog.sh"))
    assert script.exists() and os.access(script, os.X_OK)
    body = plist.read_text()
    assert str(script) in body
    assert "__MARKER__" not in script.read_text()   # placeholders rendered


# --- ollama environment persistence ---

def _schedule_into(tmp_path, monkeypatch):
    import subprocess

    import karani.cli as cli
    home = tmp_path / "home"
    (home / "Library" / "LaunchAgents").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **kw: subprocess.CompletedProcess(a, 0))
    monkeypatch.chdir(tmp_path)
    assert cli._schedule() == 0
    return home / "Library" / "LaunchAgents"


def test_local_qualify_gets_persistent_ollama_env(tmp_path, monkeypatch):
    """launchctl setenv dies at reboot, and Ollama reads its context size
    only from the launchd environment — losing it silently truncates every
    qualification prompt back to 4096 tokens."""
    from karani.config import reload_config
    cfg = tmp_path / "karani.toml"
    cfg.write_text('version = 1\n[llm.qualify]\nprovider = "local"\n')
    reload_config(cfg)
    try:
        agents = _schedule_into(tmp_path, monkeypatch)
        plist = agents / "com.karani.ollama-env.plist"
        assert plist.exists()
        body = plist.read_text()
        assert "OLLAMA_CONTEXT_LENGTH 16384" in body
        assert "OLLAMA_NUM_PARALLEL 1" in body
    finally:
        reload_config()


def test_hosted_qualify_skips_ollama_env(tmp_path, monkeypatch):
    from karani.config import reload_config
    cfg = tmp_path / "karani.toml"
    cfg.write_text('version = 1\n[llm.qualify]\nprovider = "openrouter"\n')
    reload_config(cfg)
    try:
        agents = _schedule_into(tmp_path, monkeypatch)
        assert not (agents / "com.karani.ollama-env.plist").exists()
    finally:
        reload_config()


def test_watchdog_paths_are_absolute(tmp_path, monkeypatch):
    """launchd has no working directory: a relative StandardErrorPath
    makes it refuse to spawn the job (EX_CONFIG/78), and a relative
    marker path would never be found — a permanent false alarm.

    Repo mode is where this bites: there `_workdir_and_logs()` returns a
    bare "logs", while an installed layout is absolute already."""
    (tmp_path / "karani.toml").write_text("version = 1\n")   # repo mode
    (tmp_path / "logs").mkdir()
    agents = _schedule_into(tmp_path, monkeypatch)
    script = (tmp_path / "home" / ".karani" / "watchdog.sh").read_text()
    marker = [ln for ln in script.splitlines() if ln.startswith("MARKER=")][0]
    assert '="/' in marker, f"marker path must be absolute: {marker}"

    body = (agents / "com.karani.watchdog.plist").read_text()
    err = body.split("<key>StandardErrorPath</key>")[1].split("<string>")[1]
    assert err.startswith("/"), f"log path must be absolute: {err}"
