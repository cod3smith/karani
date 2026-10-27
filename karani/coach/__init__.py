"""Phase 4 of the career-runner design: the coach.

Interview prep existed as a verb the candidate had to remember to run,
which is the wrong moment — the useful one is when an interview gets
scheduled, days before it happens. This package fires prep off the
application state machine instead (`trigger.py`), and makes the pack
worth firing: a refreshed dossier, STAR stories mined from the
candidate's own records with verified provenance, derived comp
research, and questions that cite something real.

Imports are lazy so that `Storage.set_application_status` can reach
`notify_status_change` without pulling the LLM stack into every
process that writes a status.

See docs/superpowers/specs/2026-10-23-career-runner-design.md 5.5.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from .pack import PrepPack
    from .pipeline import PrepResult

__all__ = ["build_prep_pack", "notify_status_change", "on_status_change",
           "PREP_STATUSES", "PrepPack", "PrepResult"]


async def notify_status_change(storage, job_id: int, status: str,
                               **kwargs) -> None:
    from .trigger import notify_status_change as _impl
    await _impl(storage, job_id, status, **kwargs)


async def on_status_change(storage, job_id: int, status: str, **kwargs):
    from .trigger import on_status_change as _impl
    return await _impl(storage, job_id, status, **kwargs)


async def build_prep_pack(storage, **kwargs):
    from .pipeline import build_prep_pack as _impl
    return await _impl(storage, **kwargs)


def __getattr__(name: str):
    if name == "PREP_STATUSES":
        from .trigger import PREP_STATUSES
        return PREP_STATUSES
    if name == "PrepPack":
        from .pack import PrepPack
        return PrepPack
    if name == "PrepResult":
        from .pipeline import PrepResult
        return PrepResult
    raise AttributeError(name)
