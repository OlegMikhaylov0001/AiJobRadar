"""The model's answer (spec §7.1). Strict: anything else is invalid_output."""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Axis = Annotated[int, Field(ge=0, le=4)]
Note = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=300),
]


class ScoreAxes(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    skills_match: Axis
    domain_match: Axis
    seniority_fit: Axis
    target_fit: Axis
    geo_eligible: Literal["yes", "no", "unclear"]
    employer_country_excluded: Literal["yes", "no", "unclear"]
    timezone_risk: Literal["low", "medium", "high"]
    english_risk: Literal["low", "medium", "high"]
    preferred_language: Literal["yes", "no", "unclear"]
    comp_ok: Literal["yes", "no", "unknown"]
    why_fit: Note
    main_risk: Note
    gaps: Note


def score_json_schema() -> dict[str, Any]:
    """JSON Schema passed to `claude -p --json-schema`; the same model validates the answer."""
    schema = ScoreAxes.model_json_schema()
    # Explicit overrides: the CLI contract must not depend on pydantic defaults.
    schema["additionalProperties"] = False
    schema["required"] = list(ScoreAxes.model_fields)
    return schema
