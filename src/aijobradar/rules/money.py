from aijobradar.config import RulesConfig
from aijobradar.rules.context import RuleContext
from aijobradar.rules.facts import JobFacts


def usd_per_hour(amount: float, currency: str, period: str, cfg: RulesConfig) -> float | None:
    rate = cfg.fx_to_usd.get(currency.upper())
    hours = cfg.hours_per_period.get(period)
    if rate is None or not hours:
        return None
    return amount * rate / hours


def rate_floor(f: JobFacts, ctx: RuleContext) -> str | None:
    floor = ctx.profile.min_rate_usd_per_hour
    # Only a stated ceiling proves the job pays too little; a minimum alone proves nothing.
    if floor is None or f.salary_max is None or not f.salary_currency or not f.salary_period:
        return None
    hourly = usd_per_hour(f.salary_max, f.salary_currency, f.salary_period, ctx.cfg)
    if hourly is None or hourly >= floor * (1 - ctx.cfg.rate_margin):
        return None
    return f"ceiling {f.salary_max:g} {f.salary_currency}/{f.salary_period} ≈ {hourly:.1f} USD/h"
