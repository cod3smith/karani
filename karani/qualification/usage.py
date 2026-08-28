"""Process-wide LLM token counters (roadmap 0.4).

Providers call `record()` with the API's usage payload after every
response; the run ledger stores the delta between two `snapshot()` calls
so each scheduler pass carries its own token cost. Single-process by
design — one scheduler pass is one process.
"""
from __future__ import annotations

import threading

_lock = threading.Lock()
_totals = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}


def record(usage: dict | None) -> None:
    """Add one API response's usage. Missing/partial payloads still count
    as a call — knowing a provider stopped reporting usage matters."""
    usage = usage or {}
    with _lock:
        _totals["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
        _totals["completion_tokens"] += int(usage.get("completion_tokens") or 0)
        _totals["calls"] += 1


def snapshot() -> dict:
    with _lock:
        return dict(_totals)


def delta(before: dict, after: dict) -> dict:
    return {k: after.get(k, 0) - before.get(k, 0) for k in after}


def reset() -> None:
    """Tests only — production counters are monotonic per process."""
    with _lock:
        for k in _totals:
            _totals[k] = 0
