from pathlib import Path

import pytest
from pydantic import ValidationError

from aijobradar.config import load_rules_config
from aijobradar.profile import Profile, ProfileMissing, load_profile

ROOT = Path(__file__).parent.parent


def test_repo_rules_config_loads() -> None:
    cfg = load_rules_config(ROOT / "config" / "rules.yaml")
    assert cfg.version
    assert cfg.stale_days == 30 and cfg.review_sample_size == 5
    assert cfg.fx_to_usd["USD"] == 1.0
    assert cfg.hours_per_period["year"] == 2080
    assert "US" in cfg.places and "NO" in cfg.places  # YAML must not turn NO into False
    assert "georgia" not in {a.casefold() for aliases in cfg.places.values() for a in aliases}
    assert cfg.employer_country_currencies["RU"] == ["RUB"]


def test_state_and_region_names_containing_country_names_map_to_their_country() -> None:
    cfg = load_rules_config(ROOT / "config" / "rules.yaml")
    assert "new mexico" in cfg.places["US"]
    assert "northern ireland" in cfg.places["GB"]


def test_rules_config_rejects_unknown_keys(tmp_path: Path) -> None:
    text = (ROOT / "config" / "rules.yaml").read_text() + "\nstale_dayz: 3\n"
    path = tmp_path / "rules.yaml"
    path.write_text(text)
    with pytest.raises(ValidationError):
        load_rules_config(path)


def test_example_profile_is_valid() -> None:
    profile = load_profile(ROOT / "config" / "profile.example.yaml")
    assert profile.eligible_places and profile.min_rate_usd_per_hour == 25


def test_missing_profile_raises(tmp_path: Path) -> None:
    with pytest.raises(ProfileMissing):
        load_profile(tmp_path / "profile.yaml")


def test_profile_rejects_unknown_keys_and_empty_places() -> None:
    with pytest.raises(ValidationError):
        Profile.model_validate({"eligible_places": ["PT"], "country": "PT"})
    with pytest.raises(ValidationError):
        Profile.model_validate({"eligible_places": []})
