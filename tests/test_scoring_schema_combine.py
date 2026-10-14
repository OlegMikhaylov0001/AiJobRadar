import copy
import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from aijobradar.config import ScoringConfig, load_scoring_config
from aijobradar.scoring.combine import final_score
from aijobradar.scoring.schema import ScoreAxes, score_json_schema

CONFIG_PATH = Path(__file__).parent.parent / "config" / "scoring.yaml"
CFG = load_scoring_config(CONFIG_PATH)


def _raw_config() -> dict[str, Any]:
    raw: dict[str, Any] = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    return copy.deepcopy(raw)


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
    with pytest.raises(ValidationError):
        _axes(skills_match=5)
    with pytest.raises(ValidationError):
        _axes(geo_eligible="maybe")
    with pytest.raises(ValidationError):
        _axes(why_fit="x" * 301)
    with pytest.raises(ValidationError):
        ScoreAxes.model_validate({**_axes().model_dump(), "extra": 1})


@pytest.mark.parametrize(
    "overrides",
    [
        {"skills_match": "3"},
        {"skills_match": True},
        {"skills_match": 3.0},
        {"why_fit": "   "},
    ],
)
def test_schema_rejects_loose_types(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        _axes(**overrides)


def test_json_schema_carries_cli_constraints() -> None:
    schema = score_json_schema()
    assert schema["additionalProperties"] is False
    props = schema["properties"]
    assert props["skills_match"]["minimum"] == 0
    assert props["skills_match"]["maximum"] == 4
    assert props["geo_eligible"]["enum"] == ["yes", "no", "unclear"]
    assert props["why_fit"]["maxLength"] == 300
    dumped = json.dumps(schema)
    assert "$ref" not in dumped and "$defs" not in dumped


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


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"target_fit": 2}, 87.5),
        ({"seniority_fit": 2}, 90.0),
        ({"domain_match": 2}, 90.0),
    ],
)
def test_weight_sensitivity(overrides: dict[str, Any], expected: float) -> None:
    assert final_score(_axes(**overrides), CFG) == expected


def test_weights_are_normalised() -> None:
    # Weights are divided by their sum, so all-1.0 weights mean an equal-weight mean.
    equal = CFG.model_copy(
        update={"weights": {k: 1.0 for k in CFG.weights}},
    )
    assert final_score(_axes(skills_match=2), equal) == 87.5


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"employer_country_excluded": "unclear"}, 90.0),
        ({"timezone_risk": "medium"}, 90.0),
        ({"english_risk": "high"}, 85.0),
        ({"preferred_language": "yes", "english_risk": "medium"}, 100.0),
        ({"preferred_language": "yes", "english_risk": "high"}, 90.0),
    ],
)
def test_penalties_and_bonuses(overrides: dict[str, Any], expected: float) -> None:
    assert final_score(_axes(**overrides), CFG) == expected


@pytest.mark.parametrize(
    "mutate",
    [
        lambda raw: raw.update(timeout_s=0),
        lambda raw: raw.update(max_description_chars=999),
        lambda raw: raw.update(unexpected_key=1),
        lambda raw: raw.update(
            weights={
                ("skill_match" if k == "skills_match" else k): v for k, v in raw["weights"].items()
            }
        ),
        lambda raw: raw.update(weights={k: 0.0 for k in raw["weights"]}),
        lambda raw: raw["penalties"].update(timezone_risk_extreme=10),
        lambda raw: raw["bonuses"].update(nope=5),
    ],
)
def test_scoring_config_rejects_invalid(mutate: Any) -> None:
    raw = _raw_config()
    mutate(raw)
    with pytest.raises(ValidationError):
        ScoringConfig.model_validate(raw)


@pytest.mark.parametrize("bad", ["", "../secrets", "a/b", "score v1"])
def test_prompt_version_must_be_a_plain_file_name(bad: str) -> None:
    data = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    data["prompt_version"] = bad
    with pytest.raises(ValidationError):
        ScoringConfig.model_validate(data)


def test_prompt_version_and_model_fit_the_db_columns() -> None:
    data = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    ScoringConfig.model_validate({**data, "prompt_version": "p" * 32, "model": "m" * 64})
    for field, too_long in (("prompt_version", "p" * 33), ("model", "m" * 65)):
        with pytest.raises(ValidationError):
            ScoringConfig.model_validate({**data, field: too_long})
