"""Spend accounting and the budget guard (ADR 0019).

karani shares one Anthropic account with the user's other work, so it
runs on a small allocation rather than the whole balance. Two jobs:

1. **Price every call.** Providers hand `usage.record()` a normalized
   payload; this module turns that into dollars. Cached input is billed
   at a fraction of plain input and cache writes at 1.25x, so they are
   priced separately — treating a cache read as free would tell the
   guard that caching is money for nothing.

2. **Degrade instead of overspending.** Past the cap (or with no API
   key configured) the factory hands back the local model and records a
   reason, which the hourly report surfaces. The hunt keeps running,
   free and slower — the same degradation rule the memory layer and the
   storage fallback follow. It never halts and never silently keeps
   billing.

Accounting is month-to-date from the run ledger plus whatever this
process has spent since it refreshed. One-off CLI and MCP calls outside
a scheduler pass are not in the ledger, so the figure is a floor, not an
audit — which is the safe direction for a guard.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

log = logging.getLogger(__name__)

# USD per million tokens: (input, output, cache_read). Cache writes bill
# at input x1.25 for the 5-minute TTL karani uses.
PRICES: dict[str, tuple[float, float, float]] = {
    "claude-opus-5-5":   (4.00, 20.00, 0.20),
    "claude-opus-5":     (5.00, 25.00, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20),
    "claude-sonnet-5":   (2.00, 10.00, 0.20),
    "claude-haiku-5-5":  (0.10,  0.50, 0.01),
    "claude-haiku-4-5":  (1.00,  5.00, 0.10),
}
CACHE_WRITE_MULTIPLIER = 1.25
DEFAULT_CAP_USD = 15.0

# Env var holding each provider's key. Local needs none.
PROVIDER_KEYS = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "openai": "OPENAI_API_KEY",
}

_state: dict = {"mtd_usd": 0.0, "degraded": "", "announced": ""}


def cost_usd(usage: dict | None) -> float:
    """Dollars for one API response. Unpriced models (local, or a
    provider we have no table for) cost nothing — tokens are still
    counted, so the gap is visible rather than silent."""
    usage = usage or {}
    price = PRICES.get(str(usage.get("model") or ""))
    if price is None:
        return 0.0
    rate_in, rate_out, rate_cached = price
    cache_read = int(usage.get("cache_read_tokens") or 0)
    cache_write = int(usage.get("cache_write_tokens") or 0)
    # prompt_tokens includes cache reads — they are discounted input.
    plain_in = max(0, int(usage.get("prompt_tokens") or 0) - cache_read)
    out = int(usage.get("completion_tokens") or 0)
    return (
        plain_in / 1e6 * rate_in
        + cache_read / 1e6 * rate_cached
        + cache_write / 1e6 * rate_in * CACHE_WRITE_MULTIPLIER
        + out / 1e6 * rate_out
    )


def cap() -> float:
    from karani.config import get_config
    return float(get_config().budget.monthly_usd or DEFAULT_CAP_USD)


def spent() -> float:
    """Month to date: the ledger at last refresh, plus this process."""
    from karani.qualification import usage
    return _state["mtd_usd"] + float(usage.snapshot().get("cost_usd") or 0.0)


def allows_hosted() -> bool:
    return spent() < cap()


def note_degraded(reason: str) -> None:
    if reason and reason != _state["degraded"]:
        log.warning("billed providers disabled: %s", reason)
    _state["degraded"] = reason


def degraded() -> str:
    return _state["degraded"]


def pending_announcement() -> str | None:
    """A one-line notice the first time the degradation state changes,
    then None. A spent budget lasts until the month rolls over, so
    announcing it every pass would just teach the channel to ignore
    karani."""
    current = _state["degraded"]
    if current == _state["announced"]:
        return None
    _state["announced"] = current
    if current:
        return f"karani: {current}"
    return "karani: billed providers restored — back on the hosted models"


def set_month_to_date(amount: float) -> None:
    _state["mtd_usd"] = float(amount)


async def refresh(storage) -> float:
    """Pull month-to-date spend from the run ledger. Never raises —
    a bookkeeping failure must not sink a pass; it just means the guard
    runs on this process's own spend until the next refresh."""
    try:
        _state["mtd_usd"] = float(await storage.spend_this_month() or 0.0)
    except Exception:
        log.exception("budget refresh failed; using in-process spend only")
    return _state["mtd_usd"]


def month_start(now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def reset() -> None:
    """Tests only."""
    _state["mtd_usd"] = 0.0
    _state["degraded"] = ""
    _state["announced"] = ""
