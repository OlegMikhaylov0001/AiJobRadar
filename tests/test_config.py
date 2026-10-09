from pathlib import Path

import pytest
from pydantic import ValidationError

from aijobradar.config import AppConfig, load_config, to_sqlalchemy_url


def test_neon_url_gets_psycopg_driver() -> None:
    url = "postgresql://u:p@ep-x.eu-central-1.aws.neon.tech/db?sslmode=require"
    assert to_sqlalchemy_url(url) == (
        "postgresql+psycopg://u:p@ep-x.eu-central-1.aws.neon.tech/db?sslmode=require"
    )
    assert to_sqlalchemy_url("postgres://u@h/db") == "postgresql+psycopg://u@h/db"
    assert to_sqlalchemy_url("postgresql+psycopg://h/db") == "postgresql+psycopg://h/db"


def test_repo_config_loads() -> None:
    cfg = load_config(Path(__file__).parent.parent / "config" / "sources.yaml")
    assert cfg.himalayas.parent_categories == ["Developer"]
    assert cfg.jobicy.industries == ["engineering"]
    assert "remote-back-end-programming-jobs" in cfg.wwr.feeds
    assert (cfg.dedup.title_similarity, cfg.dedup.match_window_days) == (92, 60)


def test_empty_config_uses_defaults(tmp_path: Path) -> None:
    path = tmp_path / "empty.yaml"
    path.write_text("")
    assert load_config(path) == AppConfig()


@pytest.mark.parametrize(
    "raw",
    [
        {"himalayas": {"max_page": 3}},
        {"jobicy": {"cout": 5}},
        {"wwr": {"feed": []}},
        {"dedup": {"title_similarty": 90}},
        {"dedupe": {}},
    ],
)
def test_unknown_config_keys_are_rejected(raw: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        AppConfig.model_validate(raw)


@pytest.mark.parametrize(
    "raw",
    [
        {"himalayas": {"max_pages": 0}},
        {"himalayas": {"lookback_hours": 0}},
        {"himalayas": {"page_delay_s": -1}},
        {"jobicy": {"count": 0}},
        {"jobicy": {"industries": []}},
        {"wwr": {"feeds": []}},
        {"dedup": {"title_similarity": 101}},
        {"dedup": {"match_window_days": 0}},
    ],
)
def test_values_that_would_fetch_nothing_are_rejected(raw: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        AppConfig.model_validate(raw)
