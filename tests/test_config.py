from pathlib import Path

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
