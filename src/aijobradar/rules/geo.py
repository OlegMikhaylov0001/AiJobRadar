import re
from dataclasses import dataclass

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
