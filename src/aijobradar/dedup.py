import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from rapidfuzz import fuzz


class DedupOutcome(StrEnum):
    SEEN = "seen"  # same source already reported this posting
    MERGED = "merged"  # another source (or a repost) of a job we already have
    NEW = "new"


@dataclass(frozen=True)
class Candidate:
    job_id: uuid.UUID
    title_norm: str


def best_fuzzy_match(
    title_norm: str, candidates: Sequence[Candidate], threshold: int
) -> Candidate | None:
    # token_sort_ratio, not token_set_ratio: the latter scores "backend engineer" vs
    # "senior backend engineer" as 100 and would merge different seniority levels.
    best: Candidate | None = None
    best_score = -1.0
    for candidate in candidates:
        score = fuzz.token_sort_ratio(title_norm, candidate.title_norm)
        if score >= threshold and score > best_score:
            best, best_score = candidate, score
    return best
