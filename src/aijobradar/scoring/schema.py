"""The model's answer (spec §7.1). Strict: anything else is invalid_output."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Axis = Field(ge=0, le=4)
Note = Field(min_length=1, max_length=300)


class ScoreAxes(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skills_match: int = Axis
    domain_match: int = Axis
    seniority_fit: int = Axis
    target_fit: int = Axis
    geo_eligible: Literal["yes", "no", "unclear"]
    employer_country_excluded: Literal["yes", "no", "unclear"]
    timezone_risk: Literal["low", "medium", "high"]
    english_risk: Literal["low", "medium", "high"]
    preferred_language: Literal["yes", "no", "unclear"]
    comp_ok: Literal["yes", "no", "unknown"]
    why_fit: str = Note
    main_risk: str = Note
    gaps: str = Note


def score_json_schema() -> dict[str, Any]:
    """JSON Schema passed to `claude -p --json-schema`; the same model validates the answer."""
    schema = ScoreAxes.model_json_schema()
    schema["additionalProperties"] = False
    schema["required"] = list(ScoreAxes.model_fields)
    return schema
