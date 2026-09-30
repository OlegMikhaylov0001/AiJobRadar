from pathlib import Path

import yaml
from pydantic import BaseModel, Field
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
    enabled: bool = True
    max_pages: int = 10
    lookback_days: int = 3
    parent_categories: list[str] = Field(default_factory=lambda: ["Developer"])


class JobicyConfig(BaseModel):
    enabled: bool = True
    count: int = 50
    industries: list[str] = Field(default_factory=lambda: ["engineering"])


class WwrConfig(BaseModel):
    enabled: bool = True
    feeds: list[str] = Field(
        default_factory=lambda: [
            "remote-full-stack-programming-jobs",
            "remote-back-end-programming-jobs",
            "remote-programming-jobs",
        ]
    )


class DedupConfig(BaseModel):
    title_similarity: int = 92  # rapidfuzz token_sort_ratio threshold
    match_window_days: int = 60  # also covers reposts


class AppConfig(BaseModel):
    himalayas: HimalayasConfig = Field(default_factory=HimalayasConfig)
    jobicy: JobicyConfig = Field(default_factory=JobicyConfig)
    wwr: WwrConfig = Field(default_factory=WwrConfig)
    dedup: DedupConfig = Field(default_factory=DedupConfig)


def load_config(path: Path) -> AppConfig:
    return AppConfig.model_validate(yaml.safe_load(path.read_text()) or {})
