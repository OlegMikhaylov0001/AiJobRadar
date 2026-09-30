import uuid

import pytest

from aijobradar.dedup import Candidate, best_fuzzy_match
from aijobradar.text import normalize_title


def _c(title: str) -> Candidate:
    return Candidate(job_id=uuid.uuid4(), title_norm=normalize_title(title))


def test_identical_titles_match() -> None:
    target = _c("Full Stack Engineer")
    assert best_fuzzy_match(normalize_title("Full Stack Engineer"), [target], 92) == target


def test_spelling_variants_match() -> None:
    target = _c("Senior Fullstack Engineer")
    assert best_fuzzy_match(normalize_title("Senior Full-Stack Engineer"), [target], 92) == target


def test_different_seniority_does_not_match() -> None:
    # token_set_ratio would score this 100 and merge two different roles.
    assert (
        best_fuzzy_match(normalize_title("Backend Engineer"), [_c("Senior Backend Engineer")], 92)
        is None
    )


def test_best_of_several_candidates_wins() -> None:
    near, exact = _c("Integration Engineer"), _c("Integrations Engineer")
    assert best_fuzzy_match(normalize_title("Integrations Engineer"), [near, exact], 92) == exact


def test_no_candidates() -> None:
    assert best_fuzzy_match("anything", [], 92) is None


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Software Engineer II", "Software Engineer III"),
        ("Software Engineer 2", "Software Engineer 3"),
        ("Engineer I", "Engineer II"),
        ("Account Executive US", "Account Executive UK"),
    ],
)
def test_level_or_region_marker_difference_does_not_match(a: str, b: str) -> None:
    assert best_fuzzy_match(normalize_title(a), [_c(b)], 92) is None


def test_same_markers_still_match() -> None:
    target = _c("Senior Fullstack Engineer")
    assert best_fuzzy_match(normalize_title("Senior Full-Stack Engineer"), [target], 92) == target
