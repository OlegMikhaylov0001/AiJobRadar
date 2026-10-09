import random
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from aijobradar import store
from aijobradar.db.models import Job, ReviewItem, RuleDecision
from aijobradar.models import RawJob
from aijobradar.normalize import normalize
from aijobradar.rules.engine import RULES, apply_rules, evaluate
from tests.rules_support import NOW, make_ctx, make_facts

CTX = make_ctx()


def _insert(session: Session, run_id: uuid.UUID, sid: str, **raw: object) -> Job:
    data: dict[str, object] = {
        "source": "s",
        "source_job_id": sid,
        "source_url": f"https://s/{sid}",
        "title": f"Backend Engineer {sid}",
        "company": f"Company {sid}",
    }
    data.update(raw)
    return store.insert_job(session, normalize(RawJob.model_validate(data)), run_id, NOW)


def test_rule_order_is_fixed() -> None:
    assert [rule_id for rule_id, _ in RULES] == [
        "R-NOT-REMOTE",
        "R-GEO-COUNTRY-ONLY",
        "R-GEO-RESIDENCY",
        "R-EMPLOYER-COUNTRY",
        "R-RATE-FLOOR",
        "R-NON-ENG-ROLE",
        "R-STALE",
    ]


def test_evaluate_collects_every_hit() -> None:
    facts = make_facts(title="Sales Manager (Hybrid)", location_restrictions=["United States"])
    hits = evaluate(facts, CTX)
    assert list(hits) == ["R-NOT-REMOTE", "R-GEO-COUNTRY-ONLY", "R-NON-ENG-ROLE"]
    assert all("\n" not in v and len(v) <= 200 for v in hits.values())


def test_evaluate_clean_job() -> None:
    assert evaluate(make_facts(), CTX) == {}


def test_apply_rules_sets_states_and_records(session: Session) -> None:
    run = store.start_run(session, "fetch", NOW)
    us = _insert(session, run.id, "us", location_restrictions=["United States"])
    ok = _insert(session, run.id, "ok")
    report = apply_rules(session, run_id=run.id, ctx=CTX, rng=random.Random(0))
    assert (report.evaluated, report.rejected, report.passed) == (2, 1, 1)
    assert report.by_rule == {"R-GEO-COUNTRY-ONLY": 1}
    assert session.get(Job, us.id).state == "rejected"  # type: ignore[union-attr]
    assert session.get(Job, ok.id).state == "pending_score"  # type: ignore[union-attr]
    verdicts = {d.job_id: d.verdict for d in session.scalars(select(RuleDecision))}
    assert verdicts == {us.id: "reject", ok.id: "pass"}


def test_only_new_jobs_are_evaluated(session: Session) -> None:
    run = store.start_run(session, "fetch", NOW)
    done = _insert(session, run.id, "done")
    store.set_job_state(session, done, "pending_score")
    assert apply_rules(session, run_id=run.id, ctx=CTX, rng=random.Random(0)).evaluated == 0


def test_review_sample_is_capped_and_not_duplicated(session: Session) -> None:
    run = store.start_run(session, "fetch", NOW)
    for i in range(7):
        _insert(session, run.id, f"r{i}", location_restrictions=["United States"])
    report = apply_rules(session, run_id=run.id, ctx=CTX, rng=random.Random(0))
    assert (report.rejected, report.sampled) == (7, 5)
    assert len(session.scalars(select(ReviewItem)).all()) == 5


def test_rule_error_leaves_job_new_and_is_counted(session: Session) -> None:
    run = store.start_run(session, "fetch", NOW)
    broken = _insert(session, run.id, "boom")
    fine = _insert(session, run.id, "fine")

    def explode(facts, ctx):  # type: ignore[no-untyped-def]
        if facts.job_id == broken.id:
            raise RuntimeError("bug")
        return None

    report = apply_rules(
        session, run_id=run.id, ctx=CTX, rng=random.Random(0), rules=[("R-TEST", explode)]
    )
    assert (report.evaluated, report.errors, report.first_error) == (1, 1, "RuntimeError")
    assert session.get(Job, broken.id).state == "new"  # type: ignore[union-attr]
    assert session.get(Job, fine.id).state == "pending_score"  # type: ignore[union-attr]
    assert [d.job_id for d in session.scalars(select(RuleDecision))] == [fine.id]
