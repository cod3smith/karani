"""MinIO/S3 artifact store — guarded import, env-configured, best-effort.

Defaults target karani's own compose MinIO (localhost:9010; the DataQRL
stack owns 9000). Point MINIO_ENDPOINT at any S3-compatible service.
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import re
from datetime import timedelta

log = logging.getLogger(__name__)

DEFAULT_ENDPOINT = "localhost:9010"
DEFAULT_BUCKET = "karani-applications"
PRESIGN_DAYS = int(os.getenv("MINIO_PRESIGN_DAYS", "7"))


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-") or "co"


class ArtifactStore:
    """Sync minio SDK wrapped for async use. Construct via `create()`."""

    def __init__(self, client, bucket: str):
        self._client = client
        self._bucket = bucket

    @staticmethod
    def configured() -> bool:
        return bool(os.getenv("MINIO_ACCESS_KEY")
                    and os.getenv("MINIO_SECRET_KEY"))

    @classmethod
    async def create(cls) -> "ArtifactStore | None":
        """Connect and ensure the bucket; None when unconfigured/unavailable."""
        if not cls.configured():
            return None
        try:
            from minio import Minio  # optional: uv sync --extra artifacts
        except ImportError:
            log.warning("minio SDK not installed; artifacts stay on disk "
                        "(uv sync --extra artifacts)")
            return None
        try:
            from karani.config import get_config
            from karani.config.loader import resolve
            acfg = get_config().artifacts
            client = Minio(
                resolve("MINIO_ENDPOINT", acfg.endpoint, DEFAULT_ENDPOINT),
                access_key=os.getenv("MINIO_ACCESS_KEY"),
                secret_key=os.getenv("MINIO_SECRET_KEY"),
                secure=os.getenv("MINIO_SECURE", "false").lower() == "true",
            )
            bucket = resolve("MINIO_BUCKET", acfg.bucket, DEFAULT_BUCKET)

            def _ensure():
                if not client.bucket_exists(bucket):
                    client.make_bucket(bucket)
            await asyncio.to_thread(_ensure)
            return cls(client, bucket)
        except Exception as exc:
            log.warning("artifact store unavailable (%s); files stay on "
                        "disk", exc)
            return None

    @staticmethod
    def key(job_row: dict, name: str) -> str:
        jid = job_row.get("id", 0)
        company = _slug(job_row.get("company_display")
                        or job_row.get("company") or "")
        return f"{jid:05d}-{company}/{name}"

    async def put_text(self, key: str, content: str) -> str:
        data = content.encode("utf-8")

        def _put():
            self._client.put_object(
                self._bucket, key, io.BytesIO(data), len(data),
                content_type="text/markdown; charset=utf-8",
            )
        await asyncio.to_thread(_put)
        return key

    async def presign(self, key: str) -> str:
        def _sign():
            return self._client.presigned_get_object(
                self._bucket, key, expires=timedelta(days=PRESIGN_DAYS),
            )
        return await asyncio.to_thread(_sign)

    async def store_pack(self, job_row: dict, files: dict[str, str]) -> dict:
        """Upload {name: content} for one job; returns {name: {key, url}}.

        Best-effort per file — one failed upload never sinks the pack.
        """
        out: dict[str, dict] = {}
        for name, content in files.items():
            if not content:
                continue
            try:
                key = self.key(job_row, name)
                await self.put_text(key, content)
                out[name] = {"key": key, "url": await self.presign(key)}
            except Exception as exc:
                log.warning("artifact upload failed for %s: %s", name, exc)
        return out
