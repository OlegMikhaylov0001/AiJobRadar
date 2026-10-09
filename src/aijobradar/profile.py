"""The candidate profile used by rules. Personal: lives in private/profile.yaml, never in git."""

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field


class ProfileMissing(Exception):
    pass


class Profile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Place codes from config/rules.yaml -> places, incl. ambiguous regions the candidate accepts.
    eligible_places: list[str] = Field(min_length=1)
    excluded_employer_countries: list[str] = Field(default_factory=list)
    min_rate_usd_per_hour: float | None = None


def load_profile(path: Path) -> Profile:
    if not path.is_file():
        raise ProfileMissing(str(path))
    return Profile.model_validate(yaml.safe_load(path.read_text()) or {})
