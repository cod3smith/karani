"""Everything karani knows about the candidate, assembled once.

Qualification and drafting both need the same picture — resume,
merged profile, career transcript — and it has to be byte-identical
between them or the cached prefix (ADR 0019) stops matching. One
helper, one cache, both callers.

Portfolio text is fetched by a separate (async) path and cached on
disk by the profile layer; this stays synchronous so prompt building
never blocks on the network.
"""
from __future__ import annotations

import functools
import logging

log = logging.getLogger(__name__)


@functools.lru_cache(maxsize=4)
def _assemble(resume_markdown: str) -> tuple[str, str]:
    from karani.experience import load_experience
    from karani.profile import load_profile, profile_context
    try:
        profile = profile_context(load_profile(resume_markdown))
    except Exception:
        log.warning("profile assembly failed; using the resume alone",
                    exc_info=True)
        profile = ""
    return profile, load_experience()


def candidate_context(resume_markdown: str = "") -> dict[str, str]:
    profile, experience = _assemble(resume_markdown)
    return {"profile": profile, "experience": experience}


def reset_cache() -> None:
    """Tests, and after the resume or transcript changes on disk."""
    _assemble.cache_clear()
