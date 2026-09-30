import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine, create_engine, make_url, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from aijobradar.db.migrate import upgrade

FIXTURES = Path(__file__).parent / "fixtures"
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://localhost/aijobradar_test"
)


def fixture_path(*parts: str) -> Path:
    return FIXTURES.joinpath(*parts)


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    # The schema is dropped below: refuse anything that is not an explicit test database.
    if not (make_url(TEST_DATABASE_URL).database or "").endswith("_test"):
        pytest.fail(f"TEST_DATABASE_URL must point to a *_test database, got {TEST_DATABASE_URL}")
    eng = create_engine(TEST_DATABASE_URL)
    try:
        with eng.begin() as conn:
            # Two statements, two calls: psycopg 3 rejects multi-statement prepared queries.
            conn.execute(text("DROP SCHEMA public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))
    except OperationalError as exc:
        if os.environ.get("REQUIRE_DB"):
            raise
        pytest.skip(f"Postgres unavailable ({exc.__class__.__name__}); set TEST_DATABASE_URL")
    upgrade(TEST_DATABASE_URL)
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    connection = engine.connect()
    transaction = connection.begin()
    db = Session(bind=connection, join_transaction_mode="create_savepoint")
    yield db
    db.close()
    transaction.rollback()
    connection.close()
