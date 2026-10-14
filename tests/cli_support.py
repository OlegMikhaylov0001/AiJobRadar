"""Shared by the CLI integration tests: a truncated real database, a seeding helper and a fake
scorer. The CLI commits for real, so these tests do not run inside a rolled-back transaction."""

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from aijobradar import store
from aijobradar.config import ScoringConfig
from aijobradar.db.models import Base, Job, Run
from aijobradar.models import RawJob
from aijobradar.normalize import normalize
from aijobradar.scoring.claude_cli import ScoreResult, ScoreStatus
from aijobradar.sources.jobicy import API_URL
from aijobradar.sources.wwr import FEED_URL
from tests.conftest import TEST_DATABASE_URL, fixture_path
from tests.test_scoring_engine import AXES

ROOT = Path(__file__).parent.parent
SCORING = ROOT / "config" / "scoring.yaml"
NOW = datetime(2026, 10, 9, 12, tzinfo=UTC)


# Registered as `cli_db` but importable under another name, so a test module's `cli_db`
# parameter does not shadow its own import.
@pytest.fixture(name="cli_db")
def cli_db_fixture(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)

    def wipe() -> None:
        with engine.begin() as conn:
            conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))

    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    wipe()
    yield engine
    wipe()


def seed(engine: Engine, *, profile: bool = True, jobs: int = 0) -> None:
    with Session(engine) as session, session.begin():
        run = store.start_run(session, "seed", NOW)
        if profile:
            store.add_profile_version(
                session,
                resume_md="Fictional resume: ten years of widgets.",
                targets_md="Fictional targets: remote widget work.",
                note=None,
                now=NOW,
            )
        for i in range(jobs):
            raw = RawJob(
                source="s",
                source_job_id=f"seed-{i}",
                source_url=f"https://seed.example/{i}",
                title=f"Seed Engineer {i}",
                company=f"Seed Company {i}",
                description_html=f"<p>Seed job {i}</p>",
            )
            job = store.insert_job(session, normalize(raw), run.id, NOW)
            store.set_job_state(session, job, "pending_score")


class FakeScorer:
    """Stands in for `_make_scorer`: never runs `claude`, records how it was built."""

    def __init__(self, result: ScoreResult) -> None:
        self.result = result
        self.workdirs: list[Path] = []
        self.workdir_existed_at_call: list[bool] = []
        self.messages: list[str] = []

    def __call__(self, cfg: ScoringConfig, system_prompt: str, workdir: Path) -> Any:
        assert system_prompt.strip()
        self.workdirs.append(workdir)

        def scorer(message: str) -> ScoreResult:
            self.workdir_existed_at_call.append(workdir.is_dir())
            self.messages.append(message)
            return self.result

        return scorer


def ok_result() -> ScoreResult:
    return ScoreResult(ScoreStatus.OK, AXES, 100, 10, 0.1, "{}", None)


def invalid_result() -> ScoreResult:
    return ScoreResult(ScoreStatus.INVALID_OUTPUT, None, 100, 10, 0.05, "{}", "skills_match: x")


def runs_of(engine: Engine, kind: str) -> list[Run]:
    with Session(engine) as session:
        return list(session.scalars(select(Run).where(Run.kind == kind).order_by(Run.started_at)))


def job_states(engine: Engine) -> list[str]:
    with Session(engine) as session:
        return sorted(session.scalars(select(Job.state)))


def write_run_files(tmp_path: Path, scoring: Path = SCORING) -> list[str]:
    sources = tmp_path / "sources.yaml"
    sources.write_text(
        "himalayas: {enabled: false}\nwwr: {feeds: [remote-full-stack-programming-jobs]}\n"
    )
    profile = tmp_path / "profile.yaml"
    profile.write_text("eligible_places: [PT, EU, EUROPE, EMEA, WORLDWIDE]\n")
    return [
        "run",
        "--config",
        str(sources),
        "--profile",
        str(profile),
        "--rules",
        str(ROOT / "config" / "rules.yaml"),
        "--scoring",
        str(scoring),
    ]


def mock_fetch(router: Any = respx) -> None:
    """Minimal valid Jobicy and WWR answers; `router` is the global respx or a local router."""
    router.get(API_URL).mock(
        return_value=httpx.Response(
            200, json=json.loads(fixture_path("jobicy", "engineering.json").read_text())
        )
    )
    router.get(FEED_URL.format(slug="remote-full-stack-programming-jobs")).mock(
        return_value=httpx.Response(200, content=fixture_path("wwr", "fullstack.rss").read_bytes())
    )


def seed_fresh_pending(engine: Engine, n: int) -> None:
    """`n` jobs posted an hour ago, each with a source link, waiting for the next scoring pass.

    Unlike `seed`, they are recent by the real clock, so the digest selection can pick them.
    """
    now = datetime.now(UTC)
    with Session(engine) as session, session.begin():
        run = store.start_run(session, "seed", now)
        for i in range(n):
            raw = RawJob(
                source="wwr",
                source_job_id=f"fresh-{i}",
                source_url=f"https://fresh.example/{i}",
                title=f"Fresh Engineer {i}",
                company=f"Fresh Company {i}",
                description_html=f"<p>Fresh job {i}</p>",
            )
            job = store.insert_job(session, normalize(raw), run.id, now)
            store.attach_source(session, job.id, raw, now)
            job.posted_at = now - timedelta(hours=1)
            store.set_job_state(session, job, "pending_score")
