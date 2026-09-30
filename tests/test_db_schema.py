from datetime import UTC, datetime

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, inspect, text
from sqlalchemy.orm import Session

from aijobradar.db.models import Base, Job, Run


def test_migrations_create_all_tables(engine: Engine) -> None:
    tables = set(inspect(engine).get_table_names())
    assert {"runs", "source_runs", "jobs", "job_sources", "alembic_version"} <= tables


def test_models_match_migrations(engine: Engine) -> None:
    # Guards against editing a model without writing a migration (or vice versa).
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []


def test_null_timezone_restrictions_is_sql_null(session: Session) -> None:
    now = datetime.now(UTC)
    run = Run(kind="fetch", started_at=now)
    session.add(run)
    session.flush()
    session.add(
        Job(
            content_hash="h",
            company_raw="Acme",
            company_norm="acme",
            title_raw="Engineer",
            title_norm="engineer",
            timezone_restrictions=None,
            description_text="text",
            apply_url_canonical="https://example.com/1",
            first_seen_run_id=run.id,
            first_seen_at=now,
            last_seen_at=now,
        )
    )
    session.flush()
    assert session.scalar(text("SELECT timezone_restrictions IS NULL FROM jobs")) is True
