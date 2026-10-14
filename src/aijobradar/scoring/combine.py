"""final_score is computed by code, not by the model, so a wrong score points at an axis."""

from aijobradar.config import ScoringConfig
from aijobradar.scoring.schema import ScoreAxes


def final_score(axes: ScoreAxes, cfg: ScoringConfig) -> float:
    if axes.geo_eligible == "no" or axes.employer_country_excluded == "yes":
        return 0.0
    total_weight = sum(cfg.weights.values())
    mean = sum(w * getattr(axes, name) / 4 for name, w in cfg.weights.items()) / total_weight
    score: float = 100 * mean  # annotated: getattr-derived mean is Any under mypy --strict
    flags = {
        f"geo_eligible_{axes.geo_eligible}",
        f"employer_country_excluded_{axes.employer_country_excluded}",
        f"timezone_risk_{axes.timezone_risk}",
        f"english_risk_{axes.english_risk}",
        f"comp_ok_{axes.comp_ok}",
        f"preferred_language_{axes.preferred_language}",
    }
    score -= sum(points for flag, points in cfg.penalties.items() if flag in flags)
    score += sum(points for flag, points in cfg.bonuses.items() if flag in flags)
    return round(min(100.0, max(0.0, score)), 1)
