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
_NEGATION = re.compile(r"\b(?:no|not|without|never)\b|n't", re.IGNORECASE)
_SOFT = re.compile(r"\b(?:preferred|a\s+plus|nice\s+to\s+have|desirable|bonus)\b", re.IGNORECASE)
_CLAUSE_END = re.compile(r"[.;!?](?=\s|$)")
_NEARBY_PLACE = 80
_NEGATION_REACH = 25
_SOFT_REACH = 30


@dataclass(frozen=True)
class GeoPatterns:
    country_only: tuple[re.Pattern[str], ...]
    remote_place: re.Pattern[str]
    residency: tuple[re.Pattern[str], ...]
    clearance: re.Pattern[str]
    any_place: re.Pattern[str]


def compile_geo_patterns(place_group: str) -> GeoPatterns:
    def c(template: str) -> re.Pattern[str]:
        return re.compile(template.replace("{P}", place_group), re.IGNORECASE)

    return GeoPatterns(
        country_only=tuple(c(t) for t in _COUNTRY_ONLY),
        remote_place=c(_REMOTE_PLACE),
        residency=tuple(c(t) for t in _RESIDENCY),
        clearance=re.compile(_CLEARANCE, re.IGNORECASE),
        any_place=c(r"(?<![\w.]){P}(?![\w])"),
    )


def _before(line: str, start: int, width: int) -> str:
    """Up to `width` chars before `start`, clipped to the current clause."""
    window = line[max(0, start - width) : start]
    ends = list(_CLAUSE_END.finditer(window))
    return window[ends[-1].end() :] if ends else window


def _after(line: str, end: int, width: int) -> str:
    """Up to `width` chars after `end`, clipped to the current clause."""
    window = line[end : end + width]
    stop = _CLAUSE_END.search(window)
    return window[: stop.start()] if stop else window


def _negated(line: str, match: re.Match[str]) -> bool:
    return bool(_NEGATION.search(_before(line, match.start(), _NEGATION_REACH)))


def _soft(line: str, match: re.Match[str]) -> bool:
    return bool(_SOFT.search(_after(line, match.end(), _SOFT_REACH)))


def _eligible_nearby(line: str, match: re.Match[str], ctx: "RuleContext") -> bool:
    """An eligible place in the same phrase ("US or Europe") makes the restriction non-exclusive."""
    windows = (
        _before(line, match.start(), _NEARBY_PLACE),
        _after(line, match.end(), _NEARBY_PLACE),
    )
    return any(
        ctx.gazetteer.canonical(near.group("place")) in ctx.eligible
        for window in windows
        for near in ctx.geo.any_place.finditer(window)
    )


def _foreign_place(
    patterns: tuple[re.Pattern[str], ...], text: str, ctx: "RuleContext"
) -> str | None:
    """First explicit phrase whose place is known, not eligible, and not hedged."""
    for line in text.split("\n"):
        for pattern in patterns:
            for match in pattern.finditer(line):
                code = ctx.gazetteer.canonical(match.group("place"))
                if code is None or code in ctx.eligible:
                    continue
                if (
                    _negated(line, match)
                    or _soft(line, match)
                    or _eligible_nearby(line, match, ctx)
                ):
                    continue
                return f"{code}: {match.group(0)}"
    return None


def _clearance(text: str, ctx: "RuleContext") -> str | None:
    for line in text.split("\n"):
        for match in ctx.geo.clearance.finditer(line):
            if not (_negated(line, match) or _soft(line, match)):
                return f"clearance: {match.group(0)}"
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
    return _clearance(text, ctx)
