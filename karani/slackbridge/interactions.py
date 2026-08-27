"""Button-click handling for the review flow (Slack interactivity).

Autopilot posts application packs with four buttons; each click maps to
the same state transitions the text verbs make — one code path per
transition lives in Storage, this is routing. Karani never submits an
application: "Approve" marks the pack ready, the human submits on the
portal, then "I applied" records it.
"""
from __future__ import annotations

import logging

from karani.ingestion.storage import Storage
from karani.notionsync import maybe_sync_job

log = logging.getLogger(__name__)

PACK_ACTIONS = frozenset({"pack_approve", "pack_skip",
                          "pack_applied_warm", "pack_applied_cold"})


async def handle_interaction(
    payload: dict, *, storage: Storage, memory=None,
) -> str | None:
    """Process one block_actions payload; returns the reply text.

    Unknown actions return None. Errors return a message — a bad click
    must never kill the listener (same contract as handle_command).
    """
    actions = payload.get("actions") or []
    if not actions:
        return None
    action = actions[0]
    action_id = action.get("action_id", "")
    if action_id not in PACK_ACTIONS:
        return None
    try:
        job_id = int(action.get("value", ""))
    except ValueError:
        return "That button carried no job id — record it manually."

    try:
        row = await storage.get_job(job_id)
        if not row:
            return f"Job {job_id} no longer exists."
        company = row.get("company_display") or row.get("company") or ""

        if action_id == "pack_approve":
            await storage.set_user_verdict(job_id, "apply")
            await storage.set_application_status(job_id, "ready")
            if memory is not None:
                await memory.remember_verdict(row, "apply")
            await maybe_sync_job(storage, job_id)
            return (f"Pack for *{company}* approved → `ready`. Submit it on "
                    f"the portal (<{row.get('apply_url')}|posting>), then "
                    f"hit an *I applied* button.")

        if action_id == "pack_skip":
            await storage.set_user_verdict(job_id, "skip")
            if memory is not None:
                await memory.remember_verdict(row, "skip")
            await maybe_sync_job(storage, job_id)
            return (f"Skipped *{company}* — noted for taste calibration, "
                    f"off the shortlist.")

        # pack_applied_warm / pack_applied_cold
        warm = action_id == "pack_applied_warm"
        await storage.set_application_status(job_id, "applied",
                                             warm_path=warm)
        if row.get("user_verdict") is None and memory is not None:
            await storage.set_user_verdict(job_id, "applied")
            await memory.remember_verdict(row, "applied")
        await maybe_sync_job(storage, job_id)
        path = "warm" if warm else "cold"
        return (f"Recorded: applied to *{company}* ({path} path). "
                f"Follow-up reminder fires if they go quiet.")
    except Exception as exc:
        log.exception("interaction failed: %s job=%s", action_id, job_id)
        return f"That click failed: `{exc}`. Try the text command instead."
