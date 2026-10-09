from pathlib import Path

from typer.testing import CliRunner

from aijobradar.cli import app

ROOT = Path(__file__).parent.parent


def test_help_lists_commands() -> None:
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "run" in result.output
    assert "db" in result.output


def test_run_without_profile_explains_and_exits_2(tmp_path: Path) -> None:
    missing = tmp_path / "profile.yaml"
    result = CliRunner().invoke(app, ["run", "--profile", str(missing)])
    assert result.exit_code == 2
    assert "Нет файла профиля" in result.output
    assert "config/profile.example.yaml" in result.output


def test_run_with_inconsistent_profile_exits_2(tmp_path: Path) -> None:
    profile = tmp_path / "profile.yaml"
    profile.write_text("eligible_places: [NARNIA]\n")
    result = CliRunner().invoke(
        app, ["run", "--profile", str(profile), "--rules", str(ROOT / "config" / "rules.yaml")]
    )
    assert result.exit_code == 2
    assert "Профиль не согласован" in result.output


def test_run_with_malformed_profile_exits_2_without_echoing_values(tmp_path: Path) -> None:
    profile = tmp_path / "profile.yaml"
    profile.write_text('eligible_places: [PT]\nmin_rate_usd_per_hour: "abc"\n')
    result = CliRunner().invoke(app, ["run", "--profile", str(profile)])
    assert result.exit_code == 2
    assert "некорректен" in result.output
    assert "abc" not in result.output
    assert "Traceback" not in result.output


def test_run_with_non_yaml_profile_exits_2(tmp_path: Path) -> None:
    profile = tmp_path / "profile.yaml"
    profile.write_text("eligible_places: [unclosed\n")
    result = CliRunner().invoke(app, ["run", "--profile", str(profile)])
    assert result.exit_code == 2
    assert "некорректен: не YAML" in result.output


def test_run_with_missing_rules_file_exits_2(tmp_path: Path) -> None:
    profile = tmp_path / "profile.yaml"
    profile.write_text("eligible_places: [PT]\n")
    result = CliRunner().invoke(
        app, ["run", "--profile", str(profile), "--rules", str(tmp_path / "nope.yaml")]
    )
    assert result.exit_code == 2
    assert "Ошибка в файле правил" in result.output
    assert "файл не найден" in result.output
