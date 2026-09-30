from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, inspect

from aijobradar.db.models import Base


def test_migrations_create_all_tables(engine: Engine) -> None:
    tables = set(inspect(engine).get_table_names())
    assert {"runs", "source_runs", "jobs", "job_sources", "alembic_version"} <= tables


def test_models_match_migrations(engine: Engine) -> None:
    # Guards against editing a model without writing a migration (or vice versa).
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []
