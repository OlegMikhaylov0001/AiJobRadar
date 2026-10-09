from aijobradar.rules.clauses import first_hit
from aijobradar.rules.context import RuleContext
from aijobradar.rules.facts import JobFacts


def employer_country(f: JobFacts, ctx: RuleContext) -> str | None:
    # Company, title and currency only: descriptions are left to the LLM (free text is
    # context-blind for regex).
    currency = f.salary_currency.upper() if f.salary_currency else None
    for country, patterns in ctx.employer_markers.items():
        if currency and currency in ctx.employer_currencies[country]:
            return f"{country}: currency {f.salary_currency}"
        found = first_hit(patterns, f"{f.company}\n{f.title}")
        if found:
            return f"{country}: {found}"
    return None
