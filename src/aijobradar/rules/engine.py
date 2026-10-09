import random
import uuid
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from aijobradar import store
from aijobradar.rules.basic import non_engineering_role, not_remote, stale
from aijobradar.rules.context import RuleContext
from aijobradar.rules.employer import employer_country
from aijobradar.rules.facts import JobFacts
from aijobradar.rules.geo import geo_country_only, geo_residency
from aijobradar.rules.money import rate_floor

RuleFn = Callable[[JobFacts, RuleContext], str | None]

RULES: tuple[tuple[str, RuleFn], ...] = (
    ("R-NOT-REMOTE", not_remote),
    ("R-GEO-COUNTRY-ONLY", geo_country_only),
    ("R-GEO-RESIDENCY", geo_residency),
    ("R-EMPLOYER-COUNTRY", employer_country),
    ("R-RATE-FLOOR", rate_floor),
    ("R-NON-ENG-ROLE", non_engineering_role),
    ("R-STALE", stale),
)
REVIEW_KIND = "rule_rejected_sample"


@dataclass
class RulesReport:
    evaluated: int = 0
    rejected: int = 0
    by_rule: Counter[str] = field(default_factory=Counter)
    sampled: int = 0
    errors: int = 0
    first_error: str | None = None

    @property
    def passed(self) -> int:
        return self.evaluated - self.rejected


def evaluate(
    facts: JobFacts, ctx: RuleContext, rules: Sequence[tuple[str, RuleFn]] = RULES
) -> dict[str, str]:
    """Run every rule (not just until the first hit) so the audit shows all reasons."""
    hits: dict[str, str] = {}
    for rule_id, rule in rules:
        evidence = rule(facts, ctx)
        if evidence is not None:
            hits[rule_id] = " ".join(evidence.split())[:200]
    return hits


def apply_rules(
    session: Session,
    *,
    run_id: uuid.UUID,
    ctx: RuleContext,
    rng: random.Random,
    rules: Sequence[tuple[str, RuleFn]] = RULES,
) -> RulesReport:
    report = RulesReport()
    rejected: list[uuid.UUID] = []
    for job in store.jobs_in_state(session, "new"):
        try:
            with session.begin_nested():  # a buggy rule costs this job one run, not the run
                hits = evaluate(JobFacts.from_job(job), ctx, rules)
                store.record_rule_decision(
                    session,
                    job_id=job.id,
                    run_id=run_id,
                    rules_version=ctx.cfg.version,
                    hits=hits,
                    now=ctx.now,
                )
                store.set_job_state(session, job, "rejected" if hits else "pending_score")
        except Exception as exc:
            report.errors += 1
            report.first_error = report.first_error or type(exc).__name__
            continue
        report.evaluated += 1
        if hits:
            report.rejected += 1
            report.by_rule.update(hits.keys())
            rejected.append(job.id)
    sample = rng.sample(rejected, min(ctx.cfg.review_sample_size, len(rejected)))
    for job_id in sample:
        if store.add_review_item(
            session, job_id=job_id, kind=REVIEW_KIND, run_id=run_id, now=ctx.now
        ):
            report.sampled += 1
    return report
