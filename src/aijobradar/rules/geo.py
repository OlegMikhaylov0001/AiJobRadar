import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aijobradar.rules.facts import JobFacts

if TYPE_CHECKING:
    from aijobradar.rules.context import RuleContext

# "{P}" is replaced by Gazetteer.place_group. (?<![\w.]) / (?![\w]) keep "US" from matching
# inside words; the group itself keeps short codes case-sensitive.
_COUNTRY_ONLY = (
    r"(?<![\w.]){P}(?![\w])\s*[-(]?\s*only\b",
    r"\bonly\s+(?:open\s+to\s+|accepting\s+|hiring\s+)?(?:candidates|applicants|people|residents)?"
    r"\s*(?:who\s+(?:are|live)\s+)?(?:based|located|residing|living)?\s*in\s+(?:the\s+)?{P}(?![\w])",
    r"\bmust\s+(?:be\s+)?(?:based|located|residing|reside|live|living)\s+in\s+(?:the\s+)?{P}(?![\w])",
)
_REMOTE_PLACE = r"\bremote\s*[-–(,:/]\s*{P}(?![\w])"
_RESIDENCY = (
    r"(?<![\w.]){P}(?![\w])\s+(?:residents?|citizens?|nationals?)\s+only\b",
    r"\bonly\s+(?:open\s+to\s+)?(?:residents|citizens|nationals)\s+of\s+(?:the\s+)?{P}(?![\w])",
    r"\b(?:right|authori[sz]ed|eligible|legally\s+(?:able|authori[sz]ed))\s+to\s+work\s+in\s+"
    r"(?:the\s+)?{P}(?![\w])",
    r"\bwork\s+(?:authori[sz]ation|permit)\s+(?:in|for)\s+(?:the\s+)?{P}(?![\w])",
    r"(?<![\w.]){P}(?![\w])\s+citizenship\s+(?:is\s+)?required\b",
)
_CLEARANCE = (
    r"\b(?:active|current|valid|secret|top\s+secret|ts/sci|government)\s+(?:security\s+)?clearance\b"
    r"|\bsecurity\s+clearance\s+(?:is\s+)?required\b"
    r"|\bmust\s+(?:hold|have|obtain)\s+(?:an?\s+)?(?:active\s+)?security\s+clearance\b"
)


@dataclass(frozen=True)
class GeoPatterns:
    country_only: tuple[re.Pattern[str], ...]
    remote_place: re.Pattern[str]
    residency: tuple[re.Pattern[str], ...]
    clearance: re.Pattern[str]


def compile_geo_patterns(place_group: str) -> GeoPatterns:
    def c(template: str) -> re.Pattern[str]:
        return re.compile(template.replace("{P}", place_group), re.IGNORECASE)

    return GeoPatterns(
        country_only=tuple(c(t) for t in _COUNTRY_ONLY),
        remote_place=c(_REMOTE_PLACE),
        residency=tuple(c(t) for t in _RESIDENCY),
        clearance=re.compile(_CLEARANCE, re.IGNORECASE),
    )


def _foreign_place(
    patterns: tuple[re.Pattern[str], ...], text: str, ctx: "RuleContext"
) -> str | None:
    """First phrase whose place is known and not eligible for the candidate."""
    for pattern in patterns:
        for match in pattern.finditer(text):
            code = ctx.gazetteer.canonical(match.group("place"))
            if code is not None and code not in ctx.eligible:
                return f"{code}: {match.group(0)}"
    return None


def geo_country_only(f: JobFacts, ctx: "RuleContext") -> str | None:
    if f.location_restrictions:
        codes = [ctx.gazetteer.canonical(t) for t in f.location_restrictions]
        # Any unknown token (a city, "Remote") makes the restriction ambiguous: leave it to the LLM.
        if all(c is not None for c in codes) and not any(c in ctx.eligible for c in codes):
            return "restrictions: " + ", ".join(sorted({c for c in codes if c}))
    head = f"{f.title}\n{f.location_text}"
    return _foreign_place((ctx.geo.remote_place,), head, ctx) or _foreign_place(
        ctx.geo.country_only, f"{head}\n{f.description}", ctx
    )


def geo_residency(f: JobFacts, ctx: "RuleContext") -> str | None:
    text = f"{f.title}\n{f.location_text}\n{f.description}"
    found = _foreign_place(ctx.geo.residency, text, ctx)
    if found:
        return found
    clearance = ctx.geo.clearance.search(text)
    return f"clearance: {clearance.group(0)}" if clearance else None
