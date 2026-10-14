"""What the model sees. The rendered job is also the cache key: same text, same score."""

import hashlib
from pathlib import Path

from aijobradar.db.models import Job, ProfileVersion

# No angle brackets survive in third-party text, so no tag variant (nested, spaced,
# with attributes, fullwidth) can open or close our data blocks.
_NO_BRACKETS = str.maketrans({"<": "‹", ">": "›", "＜": "‹", "＞": "›"})
_TRUNCATED = "\n[описание обрезано]"


def _clean(text: str) -> str:
    return text.translate(_NO_BRACKETS)


def _number(value: float) -> str:
    # Plain digits: ":g" turns 1500000 into "1.5e+06".
    return f"{value:f}".rstrip("0").rstrip(".")


def _salary(job: Job) -> str:
    if job.salary_min is None and job.salary_max is None:
        return "not stated"
    low = _number(job.salary_min) if job.salary_min is not None else "?"
    high = _number(job.salary_max) if job.salary_max is not None else "?"
    return f"{low}–{high} {job.salary_currency or '?'}/{job.salary_period or '?'}"


def render_job(job: Job, max_description_chars: int) -> str:
    description = job.description_text
    if len(description) > max_description_chars:
        description = description[:max_description_chars] + _TRUNCATED
    timezones = job.timezone_restrictions
    lines = [
        f"Company: {job.company_raw}",
        f"Title: {job.title_raw}",
        f"Location: {job.location_text or 'not stated'}",
        "Location restrictions: " + (", ".join(job.location_restrictions) or "none stated"),
        "Timezone restrictions (UTC offsets): "
        + (", ".join(_number(t) for t in timezones) if timezones else "none stated"),
        f"Employment type: {job.employment_type or 'not stated'}",
        f"Seniority: {job.seniority or 'not stated'}",
        f"Salary: {_salary(job)}",
        f"Posted: {job.posted_at.date().isoformat() if job.posted_at else 'not stated'}",
        "Description:",
        description,
    ]
    return _clean("\n".join(lines))


def input_hash(rendered_job: str) -> str:
    return hashlib.sha256(rendered_job.encode()).hexdigest()


def user_message(profile: ProfileVersion, rendered_job: str) -> str:
    return (
        f"<resume>\n{profile.resume_md}\n</resume>\n\n"
        f"<targets>\n{profile.targets_md}\n</targets>\n\n"
        f"<job>\n{rendered_job}\n</job>\n"
    )


def load_system_prompt(prompts_dir: Path, prompt_version: str) -> str:
    return (prompts_dir / f"{prompt_version}.md").read_text(encoding="utf-8")
