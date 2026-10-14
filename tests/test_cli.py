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
    assert "NARNIA" not in result.output


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


def test_profile_import_missing_file_exits_2(tmp_path: Path) -> None:
    targets = tmp_path / "targets.md"
    targets.write_text("# T")
    result = CliRunner().invoke(app, ["profile", "import", str(tmp_path / "nope.md"), str(targets)])
    assert result.exit_code == 2
    assert "Нет файла" in result.output


def test_profile_import_empty_file_exits_2(tmp_path: Path) -> None:
    resume, targets = tmp_path / "resume.md", tmp_path / "targets.md"
    resume.write_text("   \n")
    targets.write_text("# T")
    result = CliRunner().invoke(app, ["profile", "import", str(resume), str(targets)])
    assert result.exit_code == 2
    assert "пуст" in result.output


def test_profile_import_non_utf8_file_exits_2_without_traceback(tmp_path: Path) -> None:
    resume, targets = tmp_path / "resume.md", tmp_path / "targets.md"
    resume.write_bytes(b"\xff\xfe\xfa")
    targets.write_text("# T")
    result = CliRunner().invoke(app, ["profile", "import", str(resume), str(targets)])
    assert result.exit_code == 2
    assert f"Не удалось прочитать {resume}" in result.output
    assert "Traceback" not in result.output
    assert not isinstance(result.exception, UnicodeDecodeError)
