import re
from dataclasses import dataclass
from datetime import datetime

from aijobradar.config import RulesConfig
from aijobradar.profile import Profile
from aijobradar.rules.geo import GeoPatterns, compile_geo_patterns
from aijobradar.rules.places import Gazetteer


@dataclass(frozen=True)
class RuleContext:
    """Config + profile with every regex compiled once per run."""

    cfg: RulesConfig
    profile: Profile
    gazetteer: Gazetteer
    now: datetime
    eligible: frozenset[str]
    not_remote_title: tuple[re.Pattern[str], ...]
    not_remote_location: tuple[re.Pattern[str], ...]
    not_remote_description: tuple[re.Pattern[str], ...]
    non_eng_terms: tuple[re.Pattern[str], ...]
    eng_terms: tuple[re.Pattern[str], ...]
    employer_markers: dict[str, tuple[re.Pattern[str], ...]]
    employer_currencies: dict[str, frozenset[str]]
    geo: GeoPatterns


def _compile(patterns: list[str]) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(p, re.IGNORECASE) for p in patterns)


def _terms(terms: list[str]) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(rf"\b{re.escape(t.casefold())}\b") for t in terms)


def build_rule_context(cfg: RulesConfig, profile: Profile, now: datetime) -> RuleContext:
    gazetteer = Gazetteer(cfg.places, cfg.case_sensitive_aliases)
    unknown = set(profile.eligible_places) - gazetteer.codes
    if unknown:
        # Counts only: this message reaches CLI output and CI logs, profile values must not.
        raise ValueError(
            f"eligible_places: {len(unknown)} code(s) missing from places in config/rules.yaml"
        )
    excluded = profile.excluded_employer_countries
    unsupported = [
        c
        for c in excluded
        if c not in cfg.employer_country_markers and c not in cfg.employer_country_currencies
    ]
    if unsupported:
        # A silently inactive exclusion would be a dishonest filter.
        raise ValueError(
            f"excluded_employer_countries: {len(unsupported)} code(s) "
            "without employer markers or currencies"
        )
    return RuleContext(
        cfg=cfg,
        profile=profile,
        gazetteer=gazetteer,
        now=now,
        eligible=frozenset(profile.eligible_places),
        not_remote_title=_compile(cfg.not_remote_title_patterns),
        not_remote_location=_compile(cfg.not_remote_location_patterns),
        not_remote_description=_compile(cfg.not_remote_description_patterns),
        non_eng_terms=_terms(cfg.non_engineering_title_terms),
        eng_terms=_terms(cfg.engineering_title_terms),
        employer_markers={c: _compile(cfg.employer_country_markers.get(c, [])) for c in excluded},
        employer_currencies={
            c: frozenset(cfg.employer_country_currencies.get(c, [])) for c in excluded
        },
        geo=compile_geo_patterns(gazetteer.place_group),
    )
