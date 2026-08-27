"""User profile — the *who* the pipeline is filtering for.

Kept as code (not a YAML file) intentionally: profile changes are meaningful
enough to want a code review + git history.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .models import RoleCategory, Seniority


@dataclass
class UserProfile:
    # Seniority bands we'll take. Junior/intern get hard-dropped.
    seniority_bands: tuple[Seniority, ...] = (
        Seniority.SENIOR, Seniority.STAFF, Seniority.PRINCIPAL,
        Seniority.LEAD, Seniority.MID,
    )
    # Role categories we consider. OTHER is always dropped.
    target_categories: tuple[RoleCategory, ...] = (
        RoleCategory.SOFTWARE_ENGINEERING,
        RoleCategory.ML_AI,
        RoleCategory.DATA,
        RoleCategory.DEVOPS_SRE,
        RoleCategory.SECURITY,
        RoleCategory.RESEARCH,
        RoleCategory.ENGINEERING_LEADERSHIP,
    )
    # At least ONE of these must appear in the description (deterministic
    # word-boundary match). Otherwise the job hard-fails skill overlap.
    must_have_any: tuple[str, ...] = (
        "python", "typescript", "javascript", "go", "golang",
        "rust", "java", "kotlin", "scala", "sql",
    )
    # Bonus signals — each one boosts score. Non-vetoing.
    nice_to_have: tuple[str, ...] = (
        # AI / ML
        "machine learning", "deep learning", "llm", "large language model",
        "rag", "retrieval augmented", "pytorch", "tensorflow", "jax",
        "transformers", "hugging face", "vector database", "embedding",
        "fine-tuning", "fine tuning",
        # Bio / health
        "biotech", "bioinformatics", "genomics", "single cell", "single-cell",
        "computational biology", "drug discovery", "healthtech", "clinical",
        # Systems / infra
        "kubernetes", "aws", "gcp", "azure", "terraform", "postgres",
        "kafka", "distributed systems", "microservices",
        # Frontend / product
        "react", "next.js", "graphql",
    )
    # Skill overlap threshold. Below this, drop. Set to 0 to disable.
    min_skill_overlap: int = 1

    # Title terms that immediately reject (case-insensitive whole-word).
    excluded_title_terms: tuple[str, ...] = field(default_factory=tuple)


DEFAULT_PROFILE = UserProfile()
