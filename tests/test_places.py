import re
from pathlib import Path

import pytest

from aijobradar.config import load_rules_config
from aijobradar.rules.places import Gazetteer, normalize_place

CFG = load_rules_config(Path(__file__).parent.parent / "config" / "rules.yaml")
GAZ = Gazetteer(CFG.places, CFG.case_sensitive_aliases)


@pytest.mark.parametrize(
    ("token", "code"),
    [
        ("🇺🇸 United States of America", "US"),
        ("Anywhere in the World", "WORLDWIDE"),
        ("US only", "US"),
        ("U.S.", "US"),
        ("Bosnia and Herzegovina", "BA"),
        ("GERMANY", "DE"),
        ("US & Canada", "NORTH_AMERICA"),
        ("Narnia", None),
        ("Remote", None),
        ("New Mexico", "US"),
        ("Northern Ireland", "GB"),
    ],
)
def test_canonical(token: str, code: str | None) -> None:
    assert GAZ.canonical(token) == code


def test_normalize_place_strips_flags_only_and_punctuation() -> None:
    assert normalize_place("  🇨🇦 Canada (only)  ") == "canada"


def test_alias_mapped_to_two_codes_is_rejected() -> None:
    with pytest.raises(ValueError, match="both"):
        Gazetteer({"US": ["america"], "AMERICAS": ["America"]}, [])


def _only(text: str) -> str | None:
    pattern = re.compile(rf"(?<![\w.]){GAZ.place_group}(?![\w])\s*[-(]?\s*only\b", re.I)
    match = pattern.search(text)
    return GAZ.canonical(match.group("place")) if match else None


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("This role is US only.", "US"),
        ("Remote (USA only)", "US"),
        ("Please contact us only via email", None),  # short codes are case-sensitive
        ("Join us only if you love Rust", None),
        ("us & canada only", "NORTH_AMERICA"),
        ("EMEA only", "EMEA"),
        ("Bosnia and Herzegovina only", "BA"),
        ("New Mexico only", "US"),  # not MX: the longer alias wins
        ("Northern Ireland only", "GB"),
        ("asia pacific only", "APAC"),
        ("eastern europe only", "CEE"),
    ],
)
def test_place_group_in_text(text: str, code: str | None) -> None:
    assert _only(text) == code


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("US & Canada", "NORTH_AMERICA"),
        ("USA/Canada", "NORTH_AMERICA"),
        ("Remote - US/Canada", "NORTH_AMERICA"),
        ("Ukraine", "UA"),
        ("US", "US"),
    ],
)
def test_place_group_prefers_the_longest_alias_without_an_anchor(text: str, code: str) -> None:
    match = re.search(rf"(?<![\w.]){GAZ.place_group}(?![\w])", text, re.I)
    assert match is not None and GAZ.canonical(match.group("place")) == code
