from contextlib import suppress
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import func, select

from aijobradar.db.session import make_engine

# `daily` (run --migrate) and `labels` (db upgrade) can migrate at the same time; both wait on
# this key so two Alembic upgrades never run the same DDL. Distinct from the run/labels keys.
MIGRATE_LOCK_ID = 4_204_2028


def alembic_config(url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).parent / "alembic"))
    # configparser treats "%" as interpolation; URL-encoded passwords contain it.
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


def upgrade(url: str) -> None:
    """Apply migrations under a blocking advisory lock held on a dedicated connection."""
    engine = make_engine(url)
    try:
        with engine.connect() as conn:
            conn.execute(select(func.pg_advisory_lock(MIGRATE_LOCK_ID)))
            conn.commit()  # the session-level lock survives the commit; no transaction stays open
            try:
                command.upgrade(alembic_config(url), "head")
            finally:
                # A session-level lock dies with its connection (disposed below), so a failed
                # unlock must not hide the outcome of the upgrade.
                with suppress(Exception):
                    conn.execute(select(func.pg_advisory_unlock(MIGRATE_LOCK_ID)))
                    conn.commit()
    finally:
        engine.dispose()
