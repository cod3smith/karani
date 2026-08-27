"""Per-job application artifacts in S3-compatible object storage.

One prefix per job (resume, cover letter, pack, prep, follow-up), with
presigned links surfaced on the Slack review card — click, tweak, submit.
Optional and best-effort: unconfigured or unreachable MinIO degrades to
files-on-disk, never fails a draft. See docs/adrs/0014.
"""
from __future__ import annotations

from .store import ArtifactStore

__all__ = ["ArtifactStore"]
