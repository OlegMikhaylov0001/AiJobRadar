"""Shared helpers for rule tests. The profile is fictional (spec: no personal data in git)."""

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aijobradar.config import load_rules_config
from aijobradar.db.models import Job
from aijobradar.profile import Profile
from aijobradar.rules.context import RuleContext, build_rule_context
from aijobradar.rules.facts import JobFacts
from aijobradar.text import normalize_title

CFG = load_rules_config(Path(__file__).parent.parent / "config" / "rules.yaml")
NOW = datetime(2026, 9, 30, 6, tzinfo=UTC)
TEST_PROFILE: dict[str, Any] = {
    "eligible_places": ["PT", "EU", "EUROPE", "EMEA", "WORLDWIDE"],
    "excluded_employer_countries": ["RU"],
    "min_rate_usd_per_hour": 20,
}


def make_ctx(**profile_overrides: Any) -> RuleContext:
    profile = Profile.model_validate({**TEST_PROFILE, **profile_overrides})
    return build_rule_context(CFG, profile, NOW)


def make_facts(**overrides: Any) -> JobFacts:
    data: dict[str, Any] = {
        "job_id": uuid.uuid4(),
        "company": "Acme",
        "title": "Backend Engineer",
        "location_text": "",
        "location_restrictions": (),
        "salary_min": None,
        "salary_max": None,
        "salary_currency": None,
        "salary_period": None,
        "posted_at": None,
    }
    data.update(overrides)
    data["location_restrictions"] = tuple(data["location_restrictions"])
    data.setdefault("title_norm", normalize_title(data["title"]))
    if "title" in overrides and "title_norm" not in overrides:
        data["title_norm"] = normalize_title(data["title"])
    return JobFacts(**data)


# The strongest description wording per rule. No rule reads descriptions (free text is
# context-blind for regex; the LLM stage judges it), so none of these may ever reject.
STRONG_DESCRIPTIONS = (
    "US citizens only.",
    "This role is US only.",
    "Candidates must be authorized to work in the United States.",
    "An active security clearance is required.",
    "This is a hybrid role based in Lisbon.",
    "Оформление по ТК РФ, белая зарплата.",
)


def facts_from_job(description: str) -> JobFacts:
    """Facts built the way the engine builds them, from an (unsaved) Job with this description."""
    job = Job(
        id=uuid.uuid4(),
        company_raw="Acme",
        title_raw="Backend Engineer",
        title_norm=normalize_title("Backend Engineer"),
        location_text=None,
        location_restrictions=[],
        description_text=description,
    )
    return JobFacts.from_job(job)
