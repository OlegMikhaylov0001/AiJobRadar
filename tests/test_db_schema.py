import threading
from datetime import UTC, datetime

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, func, inspect, select, text
from sqlalchemy.orm import Session

from aijobradar.db.models import Base, Job, Run
from tests.conftest import TEST_DATABASE_URL


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


def _advisory_locks_held(engine: Engine, lock_id: int) -> int:
    # A bigint advisory key below 2**32 shows up as objid with classid 0.
    with engine.connect() as conn:
        held = conn.scalar(
            text(
                "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' "
                "AND classid = 0 AND objid = :id AND granted"
            ),
            {"id": lock_id},
        )
    return int(held or 0)


def test_migration_lock_is_its_own_key() -> None:
    from aijobradar.cli import _LABELS_LOCK_ID, _LOCK_ID
    from aijobradar.db.migrate import MIGRATE_LOCK_ID

    assert len({MIGRATE_LOCK_ID, _LOCK_ID, _LABELS_LOCK_ID}) == 3


def test_upgrade_holds_the_migration_lock_and_releases_it(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aijobradar.db import migrate

    seen: list[int] = []
    monkeypatch.setattr(
        migrate.command,
        "upgrade",
        lambda cfg, rev: seen.append(_advisory_locks_held(engine, migrate.MIGRATE_LOCK_ID)),
    )
    migrate.upgrade(TEST_DATABASE_URL)
    assert seen == [1]
    assert _advisory_locks_held(engine, migrate.MIGRATE_LOCK_ID) == 0


def test_two_upgrades_run_one_after_the_other(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aijobradar.db import migrate

    done: list[str] = []
    monkeypatch.setattr(migrate.command, "upgrade", lambda cfg, rev: done.append(rev))
    with engine.connect() as holder:  # another workflow's migration in progress
        holder.execute(select(func.pg_advisory_lock(migrate.MIGRATE_LOCK_ID)))
        holder.commit()
        worker = threading.Thread(target=migrate.upgrade, args=(TEST_DATABASE_URL,))
        worker.start()
        worker.join(timeout=0.5)
        assert worker.is_alive() and done == []  # it waits instead of running the same DDL
        holder.execute(select(func.pg_advisory_unlock(migrate.MIGRATE_LOCK_ID)))
        holder.commit()
    worker.join(timeout=10)
    assert not worker.is_alive() and done == ["head"]
