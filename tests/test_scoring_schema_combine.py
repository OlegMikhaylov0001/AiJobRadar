from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from aijobradar.config import load_scoring_config
from aijobradar.scoring.combine import final_score
from aijobradar.scoring.schema import ScoreAxes, score_json_schema

CFG = load_scoring_config(Path(__file__).parent.parent / "config" / "scoring.yaml")


def _axes(**overrides: Any) -> ScoreAxes:
    data: dict[str, Any] = {
        "skills_match": 4,
        "domain_match": 4,
        "seniority_fit": 4,
        "target_fit": 4,
        "geo_eligible": "yes",
        "employer_country_excluded": "no",
        "timezone_risk": "low",
        "english_risk": "low",
        "preferred_language": "no",
        "comp_ok": "yes",
        "why_fit": "Стек совпадает.",
        "main_risk": "Нет.",
        "gaps": "Нет.",
    }
    data.update(overrides)
    return ScoreAxes.model_validate(data)


def test_repo_scoring_config_loads() -> None:
    assert CFG.model == "claude-opus-5-5" and CFG.prompt_version == "score-v1"
    assert CFG.max_items_per_run == 80 and CFG.max_est_cost_usd_per_run == 3.0
    assert set(CFG.weights) == {"skills_match", "target_fit", "seniority_fit", "domain_match"}


def test_schema_is_strict() -> None:
    schema = score_json_schema()
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(ScoreAxes.model_fields)
    with pytest.raises(ValidationError):
        _axes(skills_match=5)
    with pytest.raises(ValidationError):
        _axes(geo_eligible="maybe")
    with pytest.raises(ValidationError):
        _axes(why_fit="x" * 301)
    with pytest.raises(ValidationError):
        ScoreAxes.model_validate({**_axes().model_dump(), "extra": 1})


def test_perfect_job_scores_100() -> None:
    assert final_score(_axes(), CFG) == 100.0


def test_weighted_mean_of_axes() -> None:
    # skills 2/4 * 0.35 + others 4/4 * 0.65 = 0.825
    assert final_score(_axes(skills_match=2), CFG) == 82.5


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"geo_eligible": "no"}, 0.0),
        ({"employer_country_excluded": "yes"}, 0.0),
        ({"geo_eligible": "unclear"}, 85.0),
        ({"timezone_risk": "high", "comp_ok": "no"}, 55.0),
        ({"english_risk": "medium"}, 95.0),
        ({"preferred_language": "yes"}, 100.0),  # clamped
    ],
)
def test_hard_zeros_penalties_and_clamp(overrides: dict[str, Any], expected: float) -> None:
    assert final_score(_axes(**overrides), CFG) == expected


def test_score_never_negative() -> None:
    worst = _axes(
        skills_match=0,
        domain_match=0,
        seniority_fit=0,
        target_fit=0,
        timezone_risk="high",
        english_risk="high",
        comp_ok="no",
        geo_eligible="unclear",
    )
    assert final_score(worst, CFG) == 0.0
