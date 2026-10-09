"""Clause-local context around a regex match: hedges never reach across a sentence boundary."""

import re
from collections.abc import Callable, Iterable

_CLAUSE_END = re.compile(r"[.;!?](?=\s|$)")
_NEGATION = re.compile(r"\b(?:no|not|without|never)\b|n't", re.IGNORECASE)
_SOFT = re.compile(
    r"\b(?:preferred|a\s+plus|nice\s+to\s+have|desirable|bonus|optional|optionally)\b",
    re.IGNORECASE,
)
# "Remote, US hours" / "US timezone overlap": a working-time window, not a location restriction.
_TEMPORAL = re.compile(
    r"\b(?:hours?|time\s*zones?|timezones?|tz|schedule|business\s+hours|working\s+hours|overlap)\b",
    re.IGNORECASE,
)
NEGATION_REACH = 25
SOFT_REACH = 30
TEMPORAL_REACH = 30
CLAUSE_REACH = 200  # sane cap for "same clause" lookups; the clause boundary is what clips

Skip = Callable[[str, re.Match[str]], bool]


def before(line: str, start: int, width: int) -> str:
    """Up to `width` chars before `start`, clipped to the current clause."""
    window = line[max(0, start - width) : start]
    ends = list(_CLAUSE_END.finditer(window))
    return window[ends[-1].end() :] if ends else window


def after(line: str, end: int, width: int) -> str:
    """Up to `width` chars after `end`, clipped to the current clause."""
    window = line[end : end + width]
    stop = _CLAUSE_END.search(window)
    return window[: stop.start()] if stop else window


def negated(line: str, match: re.Match[str]) -> bool:
    """A negation shortly before the match, in the same clause ("not US only")."""
    return bool(_NEGATION.search(before(line, match.start(), NEGATION_REACH)))


def soft(line: str, match: re.Match[str]) -> bool:
    """A softener near the match, in the same clause ("clearance preferred", "Preferred: ...")."""
    return any(
        _SOFT.search(window)
        for window in (
            before(line, match.start(), SOFT_REACH),
            after(line, match.end(), SOFT_REACH),
        )
    )


def temporal(line: str, match: re.Match[str]) -> bool:
    """A time-zone word right after the match, in the same clause ("US hours", "US time zones")."""
    return bool(_TEMPORAL.search(after(line, match.end(), TEMPORAL_REACH)))


def first_hit(
    patterns: Iterable[re.Pattern[str]], text: str, skip: Skip | None = None
) -> str | None:
    """First match, line by line, that `skip` does not discard."""
    for line in text.split("\n"):
        for pattern in patterns:
            for match in pattern.finditer(line):
                if skip is None or not skip(line, match):
                    return match.group(0)
    return None
