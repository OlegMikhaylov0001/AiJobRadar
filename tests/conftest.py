from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


def fixture_path(*parts: str) -> Path:
    return FIXTURES.joinpath(*parts)
