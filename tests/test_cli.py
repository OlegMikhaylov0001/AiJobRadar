from pathlib import Path
from typing import Any

import pytest
import respx
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from aijobradar import store
from aijobradar.cli import app
from aijobradar.config import load_rules_config
from aijobradar.db.models import Job, LlmScore
from aijobradar.models import RawJob
from aijobradar.normalize import normalize
from aijobradar.scoring.claude_cli import ScoreResult
from tests.cli_support import (
    NOW,
    ROOT,
    SCORING,
    FakeScorer,
    cli_db_fixture,  # noqa: F401  (registers the `cli_db` fixture)
    invalid_result,
    job_states,
    mock_fetch,
    ok_result,
    runs_of,
    seed,
    write_run_files,
)

NO_PROFILE_MESSAGE = (
    "Нет профиля в БД. Импортируйте: aijobradar profile import private/resume.md private/targets.md"
)


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


def test_missing_database_url_is_a_clear_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.chdir(tmp_path)  # no .env here
    result = CliRunner().invoke(
        app, ["rules", "recheck", "--rules", str(ROOT / "config" / "rules.yaml")]
    )
    assert result.exit_code == 2
    assert "Нет DATABASE_URL" in result.output and "Traceback" not in result.output


def test_rules_recheck_with_missing_rules_exits_2(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["rules", "recheck", "--rules", str(tmp_path / "nope.yaml")])
    assert result.exit_code == 2
    assert "Ошибка в файле правил" in result.output
    assert "файл не найден" in result.output


def test_run_with_broken_scoring_config_exits_2(tmp_path: Path) -> None:
    profile = tmp_path / "profile.yaml"
    profile.write_text("eligible_places: [PT]\n")
    scoring = tmp_path / "scoring.yaml"
    scoring.write_text("model: m\n")
    result = CliRunner().invoke(app, ["run", "--profile", str(profile), "--scoring", str(scoring)])
    assert result.exit_code == 2
    assert "Ошибка в файле оценки" in result.output


def test_scoring_config_error_names_the_bad_flag(tmp_path: Path) -> None:
    # The scoring config is public: its own validation messages are safe to show.
    scoring = tmp_path / "scoring.yaml"
    scoring.write_text(SCORING.read_text().replace("comp_ok_no", "comp_okk_no"))
    result = CliRunner().invoke(app, ["score", "--scoring", str(scoring)])
    assert result.exit_code == 2
    assert "Ошибка в файле оценки" in result.output
    assert "unknown penalty flag 'comp_okk_no'" in result.output


def test_profile_error_still_hides_values(tmp_path: Path) -> None:
    profile = tmp_path / "profile.yaml"
    profile.write_text("eligible_places: [PT]\nmin_rate_usd_per_hour: Fictional Secret Value\n")
    result = CliRunner().invoke(app, ["run", "--profile", str(profile)])
    assert result.exit_code == 2
    assert "Fictional Secret Value" not in result.output


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


# --- score / run --score: the CLI commits for real, so these use a truncated test database ----


def test_score_command_scores_pending_jobs(cli_db: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    seed(cli_db, jobs=2)
    fake = FakeScorer(ok_result())
    monkeypatch.setattr("aijobradar.cli._make_scorer", fake)
    result = CliRunner().invoke(app, ["score", "--scoring", str(SCORING)])
    assert result.exit_code == 0, result.output
    assert "LLM: 2 ok · ≈$0.20 (оценка по API-ценам)" in result.output
    (run,) = runs_of(cli_db, "score")
    assert run.status == "ok" and run.finished_at is not None
    assert run.counts["llm_ok"] == 2
    assert job_states(cli_db) == ["scored", "scored"]
    with Session(cli_db) as session:
        assert len(session.scalars(select(LlmScore)).all()) == 2
    # The model runs in an empty scratch directory that exists only while scoring.
    assert fake.workdir_existed_at_call == [True, True]
    assert not fake.workdirs[0].exists()
    # The (fictional) profile text reaches the model, and is stored by id, not echoed to the CLI.
    assert "Fictional resume" in fake.messages[0]
    assert "Fictional resume" not in result.output


def test_real_make_scorer_runs_claude_in_a_scratch_directory(
    cli_db: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    from aijobradar.scoring import claude_cli

    seed(cli_db, jobs=1)
    seen: list[dict[str, Any]] = []

    def fake_score(**kwargs: Any) -> ScoreResult:
        seen.append({**kwargs, "cwd_is_dir": kwargs["cwd"].is_dir()})
        return ok_result()

    monkeypatch.setattr(claude_cli, "score", fake_score)
    result = CliRunner().invoke(app, ["score", "--scoring", str(SCORING)])
    assert result.exit_code == 0, result.output
    (call,) = seen
    cwd = call["cwd"]
    assert call["cwd_is_dir"] and not cwd.exists()  # temporary: gone after the run
    assert cwd.resolve() not in (ROOT.resolve(), Path.cwd().resolve())
    assert ROOT.resolve() not in cwd.resolve().parents
    assert call["model"] == "claude-opus-5-5" and "Fictional resume" in call["user_message"]


def test_score_command_without_profile_exits_2(
    cli_db: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed(cli_db, profile=False, jobs=1)
    fake = FakeScorer(ok_result())
    monkeypatch.setattr("aijobradar.cli._make_scorer", fake)
    result = CliRunner().invoke(app, ["score", "--scoring", str(SCORING)])
    assert result.exit_code == 2
    assert NO_PROFILE_MESSAGE in result.output
    assert fake.messages == [] and runs_of(cli_db, "score") == []


def test_score_command_with_failures_is_partial(
    cli_db: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed(cli_db, jobs=1)
    monkeypatch.setattr("aijobradar.cli._make_scorer", FakeScorer(invalid_result()))
    result = CliRunner().invoke(app, ["score", "--scoring", str(SCORING)])
    assert result.exit_code == 0, result.output
    assert "LLM: 0 ok · 1 invalid_output · первая ошибка: skills_match: x · ≈$0.05" in result.output
    (run,) = runs_of(cli_db, "score")
    assert run.status == "partial" and run.counts["llm_invalid_output"] == 1
    assert job_states(cli_db) == ["pending_score"]


def test_score_command_respects_limit(cli_db: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    seed(cli_db, jobs=3)
    monkeypatch.setattr("aijobradar.cli._make_scorer", FakeScorer(ok_result()))
    result = CliRunner().invoke(app, ["score", "--scoring", str(SCORING), "--limit", "1"])
    assert result.exit_code == 0, result.output
    assert "LLM: 1 ok · 2 skipped_budget" in result.output


def test_score_command_with_broken_config_exits_2(cli_db: Engine, tmp_path: Path) -> None:
    scoring = tmp_path / "scoring.yaml"
    scoring.write_text("model: m\n")
    result = CliRunner().invoke(app, ["score", "--scoring", str(scoring)])
    assert result.exit_code == 2
    assert "Ошибка в файле оценки" in result.output


def test_score_command_with_missing_prompt_exits_2(cli_db: Engine, tmp_path: Path) -> None:
    scoring = tmp_path / "scoring.yaml"
    scoring.write_text(SCORING.read_text())  # no prompts/ next to it
    result = CliRunner().invoke(app, ["score", "--scoring", str(scoring)])
    assert result.exit_code == 2
    assert "промпт" in result.output


def test_score_command_crash_reports_class_name_only(
    cli_db: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed(cli_db, jobs=1)

    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("Fictional Job Text secret")

    monkeypatch.setattr("aijobradar.cli._make_scorer", FakeScorer(ok_result()))
    monkeypatch.setattr("aijobradar.scoring.engine.score_pending", boom)
    result = CliRunner().invoke(app, ["score", "--scoring", str(SCORING)])
    assert result.exit_code == 1
    assert "Оценка прервалась: RuntimeError" in result.output
    assert "Fictional Job Text" not in result.output
    (run,) = runs_of(cli_db, "score")
    assert run.status == "failed" and run.finished_at is not None


@respx.mock
def test_run_scores_after_fetch_and_failures_make_it_partial(
    cli_db: Engine, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seed(cli_db)
    mock_fetch()
    fake = FakeScorer(invalid_result())
    monkeypatch.setattr("aijobradar.cli._make_scorer", fake)
    result = CliRunner().invoke(app, write_run_files(tmp_path))
    assert result.exit_code == 0, result.output
    assert "Прогон: partial" in result.output
    assert result.output.splitlines()[-1].startswith("LLM: 0 ok · 2 invalid_output")
    (run,) = runs_of(cli_db, "fetch")
    assert run.status == "partial"
    assert run.counts["new"] == 4 and run.counts["llm_invalid_output"] == 2
    assert fake.workdir_existed_at_call == [True] * 2
    assert not fake.workdirs[0].exists()


@respx.mock
def test_run_with_all_jobs_scored_stays_ok(
    cli_db: Engine, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seed(cli_db)
    mock_fetch()
    monkeypatch.setattr("aijobradar.cli._make_scorer", FakeScorer(ok_result()))
    result = CliRunner().invoke(app, write_run_files(tmp_path))
    assert result.exit_code == 0, result.output
    assert "Прогон: ok" in result.output
    assert "LLM: 2 ok" in result.output
    assert runs_of(cli_db, "fetch")[0].counts["llm_ok"] == 2


@respx.mock
def test_run_keeps_fetch_results_when_scoring_crashes(
    cli_db: Engine, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seed(cli_db)
    mock_fetch()

    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("Fictional Job Text secret")

    monkeypatch.setattr("aijobradar.cli._make_scorer", FakeScorer(ok_result()))
    monkeypatch.setattr("aijobradar.scoring.engine.score_pending", boom)
    result = CliRunner().invoke(app, write_run_files(tmp_path))
    assert result.exit_code == 0, result.output
    assert "Оценка прервалась: RuntimeError" in result.output
    assert "Fictional Job Text" not in result.output
    assert "Прогон: partial" in result.output  # the fetch report is still printed
    assert "LLM:" not in result.output
    (run,) = runs_of(cli_db, "fetch")
    assert run.status == "partial" and run.counts["new"] == 4
    assert "Fictional Job Text" not in str(run.counts)
    assert job_states(cli_db).count("pending_score") == 2  # fetched and filtered, just not scored


@respx.mock
def test_run_without_profile_in_db_exits_2_before_the_network(
    cli_db: Engine, tmp_path: Path
) -> None:
    result = CliRunner().invoke(app, write_run_files(tmp_path))  # no routes: any request fails
    assert result.exit_code == 2
    assert NO_PROFILE_MESSAGE in result.output
    assert runs_of(cli_db, "fetch") == []


@respx.mock
def test_run_no_score_needs_no_profile_and_never_calls_the_model(
    cli_db: Engine, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    mock_fetch()
    fake = FakeScorer(ok_result())
    monkeypatch.setattr("aijobradar.cli._make_scorer", fake)
    result = CliRunner().invoke(app, [*write_run_files(tmp_path), "--no-score"])
    assert result.exit_code == 0, result.output
    assert "Прогон: ok" in result.output and "LLM:" not in result.output
    assert fake.messages == [] and fake.workdirs == []
    assert job_states(cli_db).count("pending_score") == 2


def test_run_with_missing_scoring_prompt_exits_2(tmp_path: Path) -> None:
    profile = tmp_path / "profile.yaml"
    profile.write_text("eligible_places: [PT]\n")
    scoring = tmp_path / "scoring.yaml"
    scoring.write_text(SCORING.read_text())
    result = CliRunner().invoke(app, ["run", "--profile", str(profile), "--scoring", str(scoring)])
    assert result.exit_code == 2
    assert "промпт" in result.output


def test_rules_recheck_reopens_rejections_of_other_rules_versions(cli_db: Engine) -> None:
    current = load_rules_config(ROOT / "config" / "rules.yaml").version
    with Session(cli_db) as session, session.begin():
        run = store.start_run(session, "seed", NOW)
        for sid, version in (("old", f"not-{current}"), ("cur", current)):
            raw = RawJob(
                source="s",
                source_job_id=sid,
                source_url=f"https://seed.example/{sid}",
                title=f"Recheck Engineer {sid}",
                company=f"Recheck Company {sid}",
            )
            job = store.insert_job(session, normalize(raw), run.id, NOW)
            store.record_rule_decision(
                session,
                job_id=job.id,
                run_id=run.id,
                rules_version=version,
                hits={"R-STALE": "x"},
                now=NOW,
            )
            store.set_job_state(session, job, "rejected")
    # No personal profile file is passed or needed: the command reads only the rules version.
    result = CliRunner().invoke(
        app, ["rules", "recheck", "--rules", str(ROOT / "config/rules.yaml")]
    )
    assert result.exit_code == 0, result.output
    assert result.output.strip() == "Вернул на проверку правилами: 1."
    assert job_states(cli_db) == ["new", "rejected"]
    with Session(cli_db) as session:
        by_title = {j.title_raw: j.state for j in session.scalars(select(Job))}
    assert by_title == {"Recheck Engineer old": "new", "Recheck Engineer cur": "rejected"}
