from typer.testing import CliRunner

from aijobradar.cli import app


def test_help_lists_commands() -> None:
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "fetch" in result.output
    assert "db" in result.output
