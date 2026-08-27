"""karani memory layer — retain, update, and recall context for decisions.

Architecture doc: docs/memory.md. ADR: docs/adrs/0009-memory-architecture.md.
"""
from __future__ import annotations

from .manager import MemoryManager

__all__ = ["MemoryManager"]
