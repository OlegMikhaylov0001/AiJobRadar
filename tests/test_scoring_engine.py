import subprocess
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from aijobradar import store
from aijobradar.config import load_scoring_config
from aijobradar.db.models import Job, LlmScore
from aijobradar.models import RawJob
from aijobradar.normalize import normalize
from aijobradar.scoring import claude_cli
from aijobradar.scoring.claude_cli import ScoreResult, ScoreStatus
from aijobradar.scoring.engine import score_pending
from aijobradar.scoring.schema import ScoreAxes
from tests.conftest import fixture_path

NOW = datetime(2026, 10, 9, 12, tzinfo=UTC)
CFG = load_scoring_config(Path(__file__).parent.parent / "config" / "scoring.yaml")
AXES = ScoreAxes.model_validate(
    {
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
        "why_fit": "Да.",
        "main_risk": "Нет.",
        "gaps": "Нет.",
    }
)


def _ok(cost: float = 0.1) -> ScoreResult:
    return ScoreResult(ScoreStatus.OK, AXES, 100, 10, cost, "{}", None)


def _fail(cost: float = 0.05) -> ScoreResult:
    return ScoreResult(ScoreStatus.INVALID_OUTPUT, None, 100, 10, cost, "{}", "skills_match: x")


def _setup(session: Session, n: int) -> tuple[uuid.UUID, Any, list[Job]]:
    run = store.start_run(session, "score", NOW)
    profile, _ = store.add_profile_version(
        session, resume_md="R", targets_md="T", note=None, now=NOW
    )
    jobs = []
    for i in range(n):
        raw = RawJob(
            source="s",
            source_job_id=str(i),
            source_url=f"https://s/{i}",
            title=f"Engineer {i}",
            company=f"Company {i}",
            description_html=f"<p>{i}</p>",
        )
        job = store.insert_job(session, normalize(raw), run.id, NOW)
        store.set_job_state(session, job, "pending_score")
        jobs.append(job)
    session.commit()
    return run.id, profile, jobs


def _scorer(*results: ScoreResult) -> Any:
    queue = list(results)
    calls: list[str] = []

    def scorer(message: str) -> ScoreResult:
        calls.append(message)
        return queue.pop(0) if len(queue) > 1 else queue[0]

    scorer.calls = calls  # type: ignore[attr-defined]
    return scorer


def test_ok_scores_and_marks_scored(session: Session) -> None:
    run_id, profile, _ = _setup(session, 2)
    report = score_pending(
        session, run_id=run_id, profile=profile, cfg=CFG, scorer=_scorer(_ok()), now=NOW
    )
    assert (report.ok, report.failed, report.est_cost_usd) == (2, 0, 0.2)
    assert {j.state for j in session.scalars(select(Job))} == {"scored"}
    rows = session.scalars(select(LlmScore)).all()
    assert len(rows) == 2 and all(r.final_score == 100.0 and r.why_fit == "Да." for r in rows)


def test_cached_score_is_reused_without_a_call(session: Session) -> None:
    run_id, profile, jobs = _setup(session, 1)
    score_pending(session, run_id=run_id, profile=profile, cfg=CFG, scorer=_scorer(_ok()), now=NOW)
    store.set_job_state(session, jobs[0], "pending_score")
    session.commit()
    scorer = _scorer(_ok())
    report = score_pending(session, run_id=run_id, profile=profile, cfg=CFG, scorer=scorer, now=NOW)
    assert (report.cached, report.ok, len(scorer.calls)) == (1, 0, 0)
    assert session.scalars(select(Job)).one().state == "scored"


def test_failure_keeps_pending_then_parks_on_the_last_attempt(session: Session) -> None:
    run_id, profile, _ = _setup(session, 1)
    for attempt in range(1, CFG.max_attempts + 1):
        report = score_pending(
            session, run_id=run_id, profile=profile, cfg=CFG, scorer=_scorer(_fail()), now=NOW
        )
        last = attempt == CFG.max_attempts
        assert (report.invalid_output, report.parked) == (1, int(last))
        expected = "score_failed" if last else "pending_score"
        assert session.scalars(select(Job)).one().state == expected
    scorer = _scorer(_fail())
    score_pending(session, run_id=run_id, profile=profile, cfg=CFG, scorer=scorer, now=NOW)
    assert scorer.calls == []


def test_item_limit_skips_the_rest(session: Session) -> None:
    run_id, profile, _ = _setup(session, 5)
    report = score_pending(
        session, run_id=run_id, profile=profile, cfg=CFG, scorer=_scorer(_ok()), now=NOW, limit=2
    )
    assert (report.ok, report.skipped_budget) == (2, 3)
    states = sorted(j.state for j in session.scalars(select(Job)))
    assert states == ["pending_score"] * 3 + ["scored"] * 2


def test_cost_cap_stops_between_waves(session: Session) -> None:
    run_id, profile, _ = _setup(session, 7)
    cfg = CFG.model_copy(update={"max_est_cost_usd_per_run": 1.0, "parallelism": 3})
    report = score_pending(
        session, run_id=run_id, profile=profile, cfg=cfg, scorer=_scorer(_ok(0.4)), now=NOW
    )
    # wave 1: 3 jobs -> 1.2 >= 1.0, so the remaining 4 are skipped
    assert (report.ok, report.skipped_budget) == (3, 4)


def test_scorer_gets_untrusted_job_inside_blocks(session: Session) -> None:
    run_id, profile, _ = _setup(session, 1)
    scorer = _scorer(_ok())
    score_pending(session, run_id=run_id, profile=profile, cfg=CFG, scorer=scorer, now=NOW)
    message = scorer.calls[0]
    assert "<resume>\nR\n</resume>" in message and "<job>" in message and "Company 0" in message


def test_raising_scorer_becomes_an_error_row_and_keeps_the_wave(session: Session) -> None:
    run_id, profile, _ = _setup(session, 3)
    cfg = CFG.model_copy(update={"parallelism": 3})

    def scorer(message: str) -> ScoreResult:
        if "Company 1" in message:
            raise ValueError("secret job text: Company 1")
        return _ok()

    report = score_pending(session, run_id=run_id, profile=profile, cfg=cfg, scorer=scorer, now=NOW)

    assert (report.ok, report.error, report.first_error) == (2, 1, "ValueError")
    rows = session.scalars(select(LlmScore).order_by(LlmScore.status)).all()
    assert [r.status for r in rows] == ["error", "ok", "ok"]
    error_row = rows[0]
    assert error_row.error == "ValueError"
    assert error_row.final_score is None and error_row.est_cost_usd == 0.0
    states = sorted(j.state for j in session.scalars(select(Job)))
    assert states == ["pending_score", "scored", "scored"]


def test_pending_jobs_are_processed_newest_posted_first(session: Session) -> None:
    run_id, profile, jobs = _setup(session, 5)
    # (posted_at age in days or None, first_seen age in hours); neither insertion order
    # nor first_seen_at-ascending order gives the expected result.
    ages: list[tuple[int | None, int]] = [(3, 5), (1, 1), (None, 0), (2, 9), (None, 3)]
    for job, (posted, seen) in zip(jobs, ages, strict=True):
        job.posted_at = NOW - timedelta(days=posted) if posted is not None else None
        job.first_seen_at = NOW - timedelta(hours=seen)
    session.commit()
    cfg = CFG.model_copy(update={"parallelism": 1})
    scorer = _scorer(_ok())
    score_pending(session, run_id=run_id, profile=profile, cfg=cfg, scorer=scorer, now=NOW)
    order = [next(f"Engineer {i}" for i in range(5) if f"Engineer {i}" in m) for m in scorer.calls]
    # dated jobs by posted_at desc, then undated ones by first_seen_at desc
    assert order == ["Engineer 1", "Engineer 3", "Engineer 0", "Engineer 2", "Engineer 4"]


def test_budget_goes_to_the_newest_postings(session: Session) -> None:
    run_id, profile, jobs = _setup(session, 3)
    for job, days in zip(jobs, (2, 0, 1), strict=True):
        job.posted_at = NOW - timedelta(days=days)
    session.commit()
    scorer = _scorer(_ok())
    score_pending(session, run_id=run_id, profile=profile, cfg=CFG, scorer=scorer, now=NOW, limit=2)
    states = {j.title_raw: j.state for j in session.scalars(select(Job))}
    assert states == {
        "Engineer 0": "pending_score",
        "Engineer 1": "scored",
        "Engineer 2": "scored",
    }


def _flaky_writes(monkeypatch: pytest.MonkeyPatch, fail_on: set[int], exc: Exception) -> None:
    real = store.record_llm_score
    calls = {"n": 0}

    def flaky(*args: Any, **kwargs: Any) -> None:
        calls["n"] += 1
        if calls["n"] in fail_on:
            raise exc
        real(*args, **kwargs)

    monkeypatch.setattr(store, "record_llm_score", flaky)


def test_transient_db_error_is_retried_so_the_paid_result_is_kept(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id, profile, _ = _setup(session, 2)
    cfg = CFG.model_copy(update={"parallelism": 1})
    _flaky_writes(monkeypatch, {2}, RuntimeError("db went away: private details"))
    scorer = _scorer(_ok())
    report = score_pending(session, run_id=run_id, profile=profile, cfg=cfg, scorer=scorer, now=NOW)
    assert (report.ok, report.error, len(scorer.calls)) == (2, 0, 2)
    assert {j.state for j in session.scalars(select(Job))} == {"scored"}
    assert [r.status for r in session.scalars(select(LlmScore))] == ["ok", "ok"]


def test_broken_db_stops_further_paid_calls(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id, profile, _ = _setup(session, 3)
    cfg = CFG.model_copy(update={"parallelism": 1})
    _flaky_writes(monkeypatch, set(range(2, 100)), RuntimeError("db went away: private details"))
    scorer = _scorer(_ok())
    report = score_pending(session, run_id=run_id, profile=profile, cfg=cfg, scorer=scorer, now=NOW)
    assert (report.ok, report.error, report.skipped_db_error) == (1, 1, 1)
    assert (len(scorer.calls), report.first_error) == (2, "RuntimeError")
    assert report.est_cost_usd == 0.2  # the lost write was still paid for
    # The failed writes did not undo the first, committed job.
    states = sorted(j.state for j in session.scalars(select(Job)))
    assert states == ["pending_score", "pending_score", "scored"]


def test_foreign_key_violation_is_a_lost_write_and_stops_the_run(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id, profile, _ = _setup(session, 3)
    cfg = CFG.model_copy(update={"parallelism": 1})
    # No ok row exists for the key: an integrity error here is not a harmless race.
    _flaky_writes(monkeypatch, {1}, IntegrityError("INSERT", {}, Exception("fk violation")))
    scorer = _scorer(_ok())
    report = score_pending(session, run_id=run_id, profile=profile, cfg=cfg, scorer=scorer, now=NOW)
    assert (report.ok, report.error, report.skipped_db_error, len(scorer.calls)) == (0, 1, 2, 1)
    assert report.first_error == "IntegrityError"
    assert {j.state for j in session.scalars(select(Job))} == {"pending_score"}


def test_race_with_a_stored_ok_score_is_adopted_and_the_run_goes_on(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id, profile, jobs = _setup(session, 2)
    cfg = CFG.model_copy(update={"parallelism": 1})
    real = store.record_llm_score

    def racing(*args: Any, **kwargs: Any) -> None:
        # A concurrent run commits the same key first; our own insert then conflicts.
        monkeypatch.setattr(store, "record_llm_score", real)
        real(*args, **kwargs)
        session.commit()
        real(*args, **kwargs)

    monkeypatch.setattr(store, "record_llm_score", racing)
    scorer = _scorer(_ok())
    report = score_pending(session, run_id=run_id, profile=profile, cfg=cfg, scorer=scorer, now=NOW)
    assert (report.ok, report.cached, report.error, report.skipped_db_error) == (1, 1, 0, 0)
    assert report.first_error is None  # a resolved race is not an error
    assert len(scorer.calls) == 2
    assert {j.state for j in session.scalars(select(Job))} == {"scored"}


def _twins(session: Session) -> tuple[uuid.UUID, Any]:
    """Two postings from different sources with identical text: one model input."""
    run = store.start_run(session, "score", NOW)
    profile, _ = store.add_profile_version(
        session, resume_md="R", targets_md="T", note=None, now=NOW
    )
    for source in ("a", "b"):
        raw = RawJob(
            source=source,
            source_job_id="1",
            source_url=f"https://{source}/1",
            title="Twin Engineer",
            company="Twin Co",
            description_html="<p>same</p>",
        )
        job = store.insert_job(session, normalize(raw), run.id, NOW)
        store.set_job_state(session, job, "pending_score")
    session.commit()
    return run.id, profile


def test_identical_inputs_share_one_paid_call(session: Session) -> None:
    run_id, profile = _twins(session)
    scorer = _scorer(_ok())
    report = score_pending(session, run_id=run_id, profile=profile, cfg=CFG, scorer=scorer, now=NOW)
    assert (len(scorer.calls), report.ok, report.cached, report.error) == (1, 1, 1, 0)
    assert report.est_cost_usd == 0.1
    assert {j.state for j in session.scalars(select(Job))} == {"scored"}
    assert [r.status for r in session.scalars(select(LlmScore))] == ["ok"]


def test_failed_shared_call_leaves_twins_pending(session: Session) -> None:
    run_id, profile = _twins(session)
    report = score_pending(
        session, run_id=run_id, profile=profile, cfg=CFG, scorer=_scorer(_fail()), now=NOW
    )
    assert (report.invalid_output, report.cached) == (1, 0)
    assert {j.state for j in session.scalars(select(Job))} == {"pending_score"}


def test_zero_cost_cap_calls_nothing(session: Session) -> None:
    run_id, profile, _ = _setup(session, 2)
    cfg = CFG.model_copy(update={"max_est_cost_usd_per_run": 0.0})
    scorer = _scorer(_ok())
    report = score_pending(session, run_id=run_id, profile=profile, cfg=cfg, scorer=scorer, now=NOW)
    assert (len(scorer.calls), report.skipped_budget) == (0, 2)


def test_runtime_errors_never_park_a_job(session: Session) -> None:
    run_id, profile, _ = _setup(session, 1)
    broken = ScoreResult(ScoreStatus.ERROR, None, 0, 0, 0.0, "{}", "api_error 400")
    for _ in range(CFG.max_attempts + 1):
        report = score_pending(
            session, run_id=run_id, profile=profile, cfg=CFG, scorer=_scorer(broken), now=NOW
        )
        assert (report.error, report.parked) == (1, 0)
    assert session.scalars(select(Job)).one().state == "pending_score"


def test_refusals_count_toward_max_attempts(session: Session) -> None:
    run_id, profile, _ = _setup(session, 1)
    refused = ScoreResult(ScoreStatus.REFUSED, None, 100, 5, 0.01, "{}", "refusal")
    for attempt in range(1, CFG.max_attempts + 1):
        report = score_pending(
            session, run_id=run_id, profile=profile, cfg=CFG, scorer=_scorer(refused), now=NOW
        )
        assert (report.refused, report.parked) == (1, int(attempt == CFG.max_attempts))
    assert session.scalars(select(Job)).one().state == "score_failed"
    scorer = _scorer(refused)
    report = score_pending(session, run_id=run_id, profile=profile, cfg=CFG, scorer=scorer, now=NOW)
    assert (report.parked, report.refused, len(scorer.calls)) == (0, 0, 0)


def test_exhausted_cli_structured_retries_park_the_job(session: Session) -> None:
    run_id, profile, _ = _setup(session, 1)
    exhausted = fixture_path("claude", "structured_retries_exhausted.json").read_text()
    runner_calls: list[list[str]] = []

    def runner(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        runner_calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=exhausted, stderr="")

    def scorer(message: str) -> ScoreResult:
        return claude_cli.score(
            user_message=message, system_prompt="SP", model="m", timeout_s=1, runner=runner
        )

    for attempt in range(1, CFG.max_attempts + 1):
        report = score_pending(
            session, run_id=run_id, profile=profile, cfg=CFG, scorer=scorer, now=NOW
        )
        last = int(attempt == CFG.max_attempts)
        assert (report.invalid_output, report.error, report.parked) == (1, 0, last)
        assert report.first_error == "error_max_structured_output_retries"
    assert len(runner_calls) == CFG.max_attempts  # one paid call per run, never two
    score_pending(session, run_id=run_id, profile=profile, cfg=CFG, scorer=scorer, now=NOW)
    assert len(runner_calls) == CFG.max_attempts
    assert session.scalars(select(Job)).one().state == "score_failed"
    assert {r.status for r in session.scalars(select(LlmScore))} == {"invalid_output"}
