"""Deterministic ATS keyword-gap scoring.

An ATS ranks the application before a human reads it. This module turns
"tailored" into a number: which of the JD's technical terms appear in the
candidate's materials, which are missing, and a coverage score persisted
per draft (`draft_keyword_coverage`) so `funnel_stats` can correlate
coverage with response rate.

Deliberately vocabulary-based, not open extraction: a curated tech vocab
matched with word boundaries is precise and free of noise ("led", "team",
and "impact" are not keywords). Extend TECH_VOCAB when a real JD surfaces
a term it misses — additions are cheap, false positives are not.
"""
from __future__ import annotations

import re

# Curated technical vocabulary. Multi-word terms allowed; matching is
# case-insensitive and word-boundary anchored (same rule as the pre-filter
# — see CLAUDE.md 4.2).
TECH_VOCAB: tuple[str, ...] = (
    # Languages
    "python", "go", "golang", "rust", "java", "scala", "kotlin", "c++",
    "typescript", "javascript", "sql", "ruby", "elixir", "swift",
    # Data / ML
    "spark", "flink", "kafka", "airflow", "dagster", "dbt", "snowflake",
    "bigquery", "redshift", "clickhouse", "databricks", "iceberg", "delta lake",
    "parquet", "duckdb", "pandas", "polars", "numpy", "pytorch", "tensorflow",
    "scikit-learn", "xgboost", "mlflow", "kubeflow", "feature store",
    "causal inference", "a/b testing", "experimentation", "recommender",
    "llm", "llms", "rag", "embeddings", "fine-tuning", "prompt engineering",
    "machine learning", "deep learning", "data engineering", "data platform",
    "data pipeline", "data pipelines", "etl", "elt", "streaming",
    "data warehouse", "data lake", "data modeling", "data quality",
    "data governance", "mlops",
    # Infra / backend
    "kubernetes", "docker", "terraform", "aws", "gcp", "azure", "lambda",
    "postgres", "postgresql", "mysql", "redis", "elasticsearch", "dynamodb",
    "mongodb", "cassandra", "rabbitmq", "grpc", "graphql", "rest",
    "microservices", "distributed systems", "event-driven", "serverless",
    "ci/cd", "observability", "prometheus", "grafana", "datadog",
    "incident response", "sre", "reliability", "scalability", "performance",
    "api design", "system design", "architecture",
    # Practice / leadership
    "mentoring", "technical leadership", "roadmap", "stakeholder",
    "cross-functional", "code review", "tdd", "agile", "on-call",
)


def _pattern(term: str) -> re.Pattern:
    return re.compile(rf"(?<![\w]){re.escape(term)}(?![\w])", re.IGNORECASE)


_COMPILED = [( _pattern(t), t) for t in TECH_VOCAB]


def extract_keywords(jd_text: str) -> list[str]:
    """JD terms from the vocabulary, in vocabulary order (stable)."""
    return [term for pat, term in _COMPILED if pat.search(jd_text or "")]


def coverage(jd_keywords: list[str], materials_text: str) -> dict:
    """How many of the JD's terms the materials hit.

    Returns {score: 0..1, matched: [...], missing: [...]}. score is 1.0
    when the JD has no recognized terms — nothing to cover is full marks.
    """
    if not jd_keywords:
        return {"score": 1.0, "matched": [], "missing": []}
    matched = [t for t in jd_keywords
               if _pattern(t).search(materials_text or "")]
    missing = [t for t in jd_keywords if t not in set(matched)]
    return {
        "score": round(len(matched) / len(jd_keywords), 3),
        "matched": matched,
        "missing": missing,
    }
