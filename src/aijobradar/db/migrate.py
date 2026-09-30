from pathlib import Path

from alembic import command
from alembic.config import Config


def alembic_config(url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).parent / "alembic"))
    # configparser treats "%" as interpolation; URL-encoded passwords contain it.
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


def upgrade(url: str) -> None:
    command.upgrade(alembic_config(url), "head")
