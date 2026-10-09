"""Clause-local context around a regex match: hedges never reach across a sentence boundary."""

import re

_CLAUSE_END = re.compile(r"[.;!?](?=\s|$)")
_NEGATION = re.compile(r"\b(?:no|not|without|never)\b|n't", re.IGNORECASE)
_SOFT = re.compile(r"\b(?:preferred|a\s+plus|nice\s+to\s+have|desirable|bonus)\b", re.IGNORECASE)
NEGATION_REACH = 25
SOFT_REACH = 30


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
    """A softener shortly after the match, in the same clause ("clearance preferred")."""
    return bool(_SOFT.search(after(line, match.end(), SOFT_REACH)))
