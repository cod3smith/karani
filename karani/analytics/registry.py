"""Strategy variants as stored records, plus arm assignment.

A variant is not a flag in a config file, because the thing that makes
it useful is that it outlives the config: six weeks from now you need
to know what "outcome-first" meant when it was applied, not what the
file says today. So each one is a row, with the guidance text that
defines the treatment.

Assignment is a deterministic function of the job id over the sorted
active arms. Three properties, all of which matter more than
cleverness: it is reproducible (the same job always lands on the same
arm, so a re-draft does not silently switch treatment), it is balanced
across arms without keeping counters, and it has no state to get out
of sync with the ledger.

This is the "bandit over a handful of arms" from the design doc in its
uniform-allocation form. Thompson sampling or epsilon-greedy would
allocate more traffic to the leader — tempting, and wrong at this
sample size, where the leader is whichever arm got lucky first.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from .models import StrategyVariant

log = logging.getLogger(__name__)

VARIANT_SCHEMA = """
CREATE TABLE IF NOT EXISTS strategy_variants (
    name TEXT PRIMARY KEY,
    dimension TEXT NOT NULL DEFAULT 'positioning',
    description TEXT NOT NULL DEFAULT '',
    guidance TEXT NOT NULL DEFAULT '',
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE strategy_variants ADD COLUMN IF NOT EXISTS guidance TEXT
    NOT NULL DEFAULT '';
ALTER TABLE strategy_variants ADD COLUMN IF NOT EXISTS active BOOLEAN
    NOT NULL DEFAULT TRUE;
"""


class VariantRegistry:
    """CRUD over `strategy_variants`, on either Storage backend."""

    def __init__(self, storage) -> None:
        self.storage = storage
        self._schema_ready = False

    @property
    def _pool(self):
        return getattr(self.storage, "pool", None)

    def _mem(self) -> dict[str, StrategyVariant]:
        rows = getattr(self.storage, "_analytics_variants", None)
        if rows is None:
            rows = {}
            self.storage._analytics_variants = rows
        return rows

    async def ensure_schema(self) -> None:
        if self._schema_ready:
            return
        if self._pool is not None:
            async with self._pool.acquire() as conn:
                await conn.execute(VARIANT_SCHEMA)
        self._schema_ready = True

    async def register(self, variant: StrategyVariant) -> StrategyVariant:
        """Insert or update a variant. Re-registering an existing name
        reactivates it and replaces its guidance — which means outcomes
        recorded before the change sit under a label whose meaning
        moved. Rename instead of rewriting a live arm."""
        await self.ensure_schema()
        variant = variant.model_copy(update={
            "created_at": variant.created_at or datetime.now(timezone.utc)})
        if self._pool is None:
            self._mem()[variant.name] = variant
            return variant
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO strategy_variants
                    (name, dimension, description, guidance, active)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (name) DO UPDATE SET
                    dimension = EXCLUDED.dimension,
                    description = EXCLUDED.description,
                    guidance = EXCLUDED.guidance,
                    active = EXCLUDED.active
                """,
                variant.name, variant.dimension, variant.description,
                variant.guidance, variant.active,
            )
        return variant

    async def all(self) -> list[StrategyVariant]:
        await self.ensure_schema()
        if self._pool is None:
            rows = list(self._mem().values())
        else:
            async with self._pool.acquire() as conn:
                records = await conn.fetch(
                    "SELECT * FROM strategy_variants ORDER BY name")
            rows = [StrategyVariant.model_validate(dict(r)) for r in records]
        return sorted(rows, key=lambda v: v.name)

    async def active(self, dimension: str | None = None
                     ) -> list[StrategyVariant]:
        return [v for v in await self.all()
                if v.active and (dimension is None or v.dimension == dimension)]

    async def get(self, name: str) -> StrategyVariant | None:
        return next((v for v in await self.all() if v.name == name), None)

    async def deactivate(self, name: str) -> bool:
        """Retire an arm without deleting it — its applications keep
        their label, and the history stays readable."""
        existing = await self.get(name)
        if existing is None:
            return False
        await self.register(existing.model_copy(update={"active": False}))
        return True

    async def assign(self, job_id: int, *, dimension: str = "positioning"
                     ) -> StrategyVariant | None:
        """Pick this application's arm. None when no arm is active."""
        arms = await self.active(dimension)
        if not arms:
            return None
        return arms[int(job_id) % len(arms)]

    async def guidance_map(self) -> dict[str, str]:
        return {v.name: v.guidance for v in await self.all()}


__all__ = ["VARIANT_SCHEMA", "VariantRegistry"]
