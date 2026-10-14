from pathlib import Path
from typing import Literal, Self, get_args, get_origin

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from aijobradar.scoring.schema import ScoreAxes


def to_sqlalchemy_url(url: str) -> str:
    """Neon and most providers hand out libpq URLs; SQLAlchemy needs the psycopg driver name."""
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    return url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    user_agent: str = "AiJobRadar/0.1 (personal job monitor)"
    http_timeout_s: float = 30.0
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None

    @property
    def sqlalchemy_url(self) -> str:
        return to_sqlalchemy_url(self.database_url)


class HimalayasConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    max_pages: int = Field(500, ge=1)
    lookback_hours: int = Field(30, ge=1)
    # 500 pages at <= 12 pages an hour is ~41 h: a longer cap could not be walked in full.
    max_lookback_hours: int = Field(40, ge=1)
    page_delay_s: float = Field(0.5, ge=0)
    parent_categories: list[str] = Field(default_factory=lambda: ["Developer"])

    @model_validator(mode="after")
    def _check_lookback_cap(self) -> Self:
        if self.max_lookback_hours < self.lookback_hours:
            raise ValueError("max_lookback_hours must be >= lookback_hours")
        return self


class JobicyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    count: int = Field(50, ge=1)
    industries: list[str] = Field(default_factory=lambda: ["engineering"], min_length=1)


class WwrConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    feeds: list[str] = Field(
        default_factory=lambda: [
            "remote-full-stack-programming-jobs",
            "remote-back-end-programming-jobs",
            "remote-programming-jobs",
        ],
        min_length=1,
    )


class DedupConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title_similarity: int = Field(92, ge=0, le=100)  # rapidfuzz token_sort_ratio threshold
    match_window_days: int = Field(60, ge=1)  # also covers reposts


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    himalayas: HimalayasConfig = Field(default_factory=HimalayasConfig)
    jobicy: JobicyConfig = Field(default_factory=JobicyConfig)
    wwr: WwrConfig = Field(default_factory=WwrConfig)
    dedup: DedupConfig = Field(default_factory=DedupConfig)


def load_config(path: Path) -> AppConfig:
    return AppConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})


class RulesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = Field(min_length=1)
    stale_days: int
    rate_margin: float
    review_sample_size: int
    min_plausible_usd_per_hour: float = Field(ge=0)
    fx_to_usd: dict[str, float]
    hours_per_period: dict[str, float]
    places: dict[str, list[str]]
    case_sensitive_aliases: list[str]
    not_remote_title_patterns: list[str]
    not_remote_location_patterns: list[str]
    non_engineering_title_terms: list[str]
    engineering_title_terms: list[str]
    employer_country_markers: dict[str, list[str]]
    employer_country_currencies: dict[str, list[str]]


def load_rules_config(path: Path) -> RulesConfig:
    return RulesConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


# Weighted 0-4 axes, and every "<categorical field>_<value>" flag the model can emit.
_SCORING_AXES = frozenset({"skills_match", "domain_match", "seniority_fit", "target_fit"})
_SCORING_FLAGS = frozenset(
    f"{name}_{value}"
    for name, field in ScoreAxes.model_fields.items()
    if get_origin(field.annotation) is Literal
    for value in get_args(field.annotation)
)


class ScoringConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str = Field(min_length=1, max_length=64)  # llm_scores.model is String(64)
    # A file name under prompts/; llm_scores.prompt_version is String(32).
    prompt_version: str = Field(pattern=r"^[A-Za-z0-9._-]+$", max_length=32)
    timeout_s: float = Field(gt=0)
    parallelism: int = Field(ge=1)
    max_items_per_run: int = Field(ge=1)
    max_est_cost_usd_per_run: float = Field(gt=0)
    max_attempts: int = Field(ge=1)
    max_description_chars: int = Field(ge=1000)
    # Digest selection (spec §6.5). The threshold is a starting point until eval (stage 5).
    threshold: float = Field(60.0, ge=0, le=100)
    top_n: int = Field(5, ge=0, le=10)
    explore_n: int = Field(2, ge=0, le=5)
    # Older postings (by posted_at, else first seen) are never sent.
    max_age_days: int = Field(14, ge=1)
    weights: dict[str, float]
    penalties: dict[str, float]
    bonuses: dict[str, float]

    @model_validator(mode="after")
    def _check_scoring_keys(self) -> Self:
        unknown_axes = set(self.weights) - _SCORING_AXES
        if unknown_axes:
            raise ValueError(f"unknown weight axis {sorted(unknown_axes)[0]!r}")
        missing_axes = _SCORING_AXES - set(self.weights)
        if missing_axes:
            raise ValueError(f"missing weight axis {sorted(missing_axes)[0]!r}")
        if sum(self.weights.values()) <= 0:
            raise ValueError("weights must sum to a positive number")
        for section, flags in (("penalty", self.penalties), ("bonus", self.bonuses)):
            for flag in flags:
                if flag not in _SCORING_FLAGS:
                    raise ValueError(f"unknown {section} flag {flag!r}")
        return self


def load_scoring_config(path: Path) -> ScoringConfig:
    return ScoringConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
