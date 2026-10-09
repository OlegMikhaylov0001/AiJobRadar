from aijobradar.rules.context import RuleContext
from aijobradar.rules.facts import JobFacts


def employer_country(f: JobFacts, ctx: RuleContext) -> str | None:
    text = f"{f.company}\n{f.title}\n{f.description}"
    currency = f.salary_currency.upper() if f.salary_currency else None
    for country, patterns in ctx.employer_markers.items():
        if currency and currency in ctx.employer_currencies[country]:
            return f"{country}: currency {f.salary_currency}"
        for pattern in patterns:
            match = pattern.search(text)
            if match:
                return f"{country}: {match.group(0)}"
    return None
