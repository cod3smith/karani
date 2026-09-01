"""Config schema — frozen, validated, defaults == pre-config behavior."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, model_validator


class ProfileCfg(BaseModel, frozen=True):
    roles: tuple[str, ...] = (
        "software_engineering", "ml_ai", "data", "devops_sre",
        "security", "research", "engineering_leadership",
    )
    seniority: tuple[str, ...] = ("mid", "senior", "staff",
                                  "principal", "lead")
    must_have_any: tuple[str, ...] = (
        "python", "typescript", "javascript", "go", "golang",
        "rust", "java", "kotlin", "scala", "sql",
    )
    nice_to_have: tuple[str, ...] = ()   # () = keep built-in defaults
    exclude_titles: tuple[str, ...] = ()  # appended to built-in exclusions
    min_skill_overlap: int = 1


class Shape(BaseModel, frozen=True):
    """One qualifying posture — e.g. global-remote-at-SF-bands, or
    relocation-to-EU/Japan. Shapes are additive: a role qualifies if it
    fits ANY shape."""
    name: str
    remote: Literal["required", "preferred", "any"] = "required"
    comp_floor_usd: int | None = None
    destinations: tuple[str, ...] = ()
    requires: tuple[str, ...] = ()  # e.g. "visa_sponsorship"


class PositioningCfg(BaseModel, frozen=True):
    """Templated into every prompt — this is what de-personalizes the
    prompts for other users."""
    based_in: str = "Nairobi, Kenya"
    candidate: str = "a senior/staff engineer"
    narrative: str = (
        "targeting fully-remote roles at globally-distributed companies "
        "that pay at San Francisco bands (~$160k+ base, ideally $220k+ "
        "TC) regardless of candidate location, or roles that sponsor a "
        "visa and relocation (EU and Japan preferred, local "
        "top-of-market comp acceptable there)"
    )
    excluded_domains: str = "computational-biology / bioinformatics roles"


class TargetCfg(BaseModel, frozen=True):
    source: Literal["greenhouse", "lever", "ashby", "workable"]
    slug: str
    display_name: str = ""
    notes: str = ""


class LlmTask(BaseModel, frozen=True):
    # Any name in the provider registry: openrouter | openai | anthropic
    # | local | anything added via register_provider() (ADR 0017).
    provider: str | None = None
    model: str | None = None
    effort: str | None = None
    max_tokens: int | None = None
    # For OpenAI-compatible providers: point at Groq/Together/vLLM/...
    base_url: str | None = None
    # NAME of the env var holding the key (e.g. "GROQ_API_KEY") — the
    # key itself never goes in karani.toml.
    api_key_env: str | None = None
    timeout: int | None = None


class LlmCfg(BaseModel, frozen=True):
    default: LlmTask = LlmTask()
    qualify: LlmTask = LlmTask()
    draft: LlmTask = LlmTask()
    humanize: LlmTask = LlmTask()
    tailor: LlmTask = LlmTask()
    prep: LlmTask = LlmTask()
    followup: LlmTask = LlmTask()
    agent: LlmTask = LlmTask()

    def for_task(self, task: str) -> LlmTask:
        specific: LlmTask = getattr(self, task, LlmTask())
        merged = {
            name: getattr(specific, name) if getattr(specific, name)
            is not None else getattr(self.default, name)
            for name in LlmTask.model_fields
        }
        return LlmTask(**merged)


class MemoryCfg(BaseModel, frozen=True):
    mode: Literal["off", "basic", "mem0"] | None = None
    pg_url: str | None = None
    ollama_url: str | None = None
    llm_model: str | None = None
    embedder_model: str | None = None
    embed_dims: int | None = None
    collection: str | None = None
    user_id: str | None = None


class SlackCfg(BaseModel, frozen=True):
    channel: str | None = None
    allowed_users: tuple[str, ...] = ()  # empty = allow all (single-user)


class NotionCfg(BaseModel, frozen=True):
    database_id: str | None = None


class ArtifactsCfg(BaseModel, frozen=True):
    endpoint: str | None = None
    bucket: str | None = None
    secure: bool | None = None
    presign_days: int | None = None


class AutopilotCfg(BaseModel, frozen=True):
    min_fit: int | None = None
    max_drafts: int | None = None
    max_drafts_per_day: int | None = None
    # Agent-mode double-check of each candidate's geo/visa/comp claims
    # before pack budget is spent (ADR 0016). Billed: one agent call per
    # candidate. Off by default.
    verify: bool = False


class HuntCfg(BaseModel, frozen=True):
    qualify_limit: int = 50
    min_comp_usd: int | None = None
    target_comp_usd: int | None = None


class KaraniConfig(BaseModel, frozen=True):
    version: int = 1
    profile: ProfileCfg = ProfileCfg()
    shapes: tuple[Shape, ...] = ()
    positioning: PositioningCfg = PositioningCfg()
    targets: tuple[TargetCfg, ...] = ()  # () = built-in curated list
    llm: LlmCfg = LlmCfg()
    memory: MemoryCfg = MemoryCfg()
    slack: SlackCfg = SlackCfg()
    notion: NotionCfg = NotionCfg()
    artifacts: ArtifactsCfg = ArtifactsCfg()
    autopilot: AutopilotCfg = AutopilotCfg()
    hunt: HuntCfg = HuntCfg()

    @model_validator(mode="after")
    def _check_version(self) -> "KaraniConfig":
        if self.version != 1:
            raise ValueError(
                f"karani.toml version {self.version} is newer than this "
                f"karani understands (1) — upgrade karani."
            )
        return self
