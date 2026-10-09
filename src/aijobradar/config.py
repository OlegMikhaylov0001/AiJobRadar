from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    @property
    def sqlalchemy_url(self) -> str:
        return to_sqlalchemy_url(self.database_url)


class HimalayasConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    max_pages: int = Field(500, ge=1)
    lookback_hours: int = Field(30, ge=1)
    page_delay_s: float = Field(0.5, ge=0)
    parent_categories: list[str] = Field(default_factory=lambda: ["Developer"])


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
    not_remote_description_patterns: list[str]
    non_engineering_title_terms: list[str]
    engineering_title_terms: list[str]
    employer_country_markers: dict[str, list[str]]
    employer_country_currencies: dict[str, list[str]]


def load_rules_config(path: Path) -> RulesConfig:
    return RulesConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
