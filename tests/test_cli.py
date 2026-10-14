import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from aijobradar import store
from aijobradar.cli import app
from aijobradar.config import ScoringConfig, load_rules_config
from aijobradar.db.models import Base, Job, LlmScore, Run
from aijobradar.models import RawJob
from aijobradar.normalize import normalize
from aijobradar.scoring.claude_cli import ScoreResult, ScoreStatus
from aijobradar.sources.jobicy import API_URL
from aijobradar.sources.wwr import FEED_URL
from tests.conftest import TEST_DATABASE_URL, fixture_path
from tests.test_scoring_engine import AXES

ROOT = Path(__file__).parent.parent
SCORING = ROOT / "config" / "scoring.yaml"
NOW = datetime(2026, 10, 9, 12, tzinfo=UTC)
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


@pytest.fixture
def cli_db(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)

    def wipe() -> None:
        with engine.begin() as conn:
            conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))

    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    wipe()
    yield engine
    wipe()


def _seed(engine: Engine, *, profile: bool = True, jobs: int = 0) -> None:
    with Session(engine) as session, session.begin():
        run = store.start_run(session, "seed", NOW)
        if profile:
            store.add_profile_version(
                session,
                resume_md="Fictional resume: ten years of widgets.",
                targets_md="Fictional targets: remote widget work.",
                note=None,
                now=NOW,
            )
        for i in range(jobs):
            raw = RawJob(
                source="s",
                source_job_id=f"seed-{i}",
                source_url=f"https://seed.example/{i}",
                title=f"Seed Engineer {i}",
                company=f"Seed Company {i}",
                description_html=f"<p>Seed job {i}</p>",
            )
            job = store.insert_job(session, normalize(raw), run.id, NOW)
            store.set_job_state(session, job, "pending_score")


class _FakeScorer:
    """Stands in for `_make_scorer`: never runs `claude`, records how it was built."""

    def __init__(self, result: ScoreResult) -> None:
        self.result = result
        self.workdirs: list[Path] = []
        self.workdir_existed_at_call: list[bool] = []
        self.messages: list[str] = []

    def __call__(self, cfg: ScoringConfig, system_prompt: str, workdir: Path) -> Any:
        assert system_prompt.strip()
        self.workdirs.append(workdir)

        def scorer(message: str) -> ScoreResult:
            self.workdir_existed_at_call.append(workdir.is_dir())
            self.messages.append(message)
            return self.result

        return scorer


def _ok() -> ScoreResult:
    return ScoreResult(ScoreStatus.OK, AXES, 100, 10, 0.1, "{}", None)


def _invalid() -> ScoreResult:
    return ScoreResult(ScoreStatus.INVALID_OUTPUT, None, 100, 10, 0.05, "{}", "skills_match: x")


def _runs(engine: Engine, kind: str) -> list[Run]:
    with Session(engine) as session:
        return list(session.scalars(select(Run).where(Run.kind == kind).order_by(Run.started_at)))


def _states(engine: Engine) -> list[str]:
    with Session(engine) as session:
        return sorted(session.scalars(select(Job.state)))


def test_score_command_scores_pending_jobs(cli_db: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed(cli_db, jobs=2)
    fake = _FakeScorer(_ok())
    monkeypatch.setattr("aijobradar.cli._make_scorer", fake)
    result = CliRunner().invoke(app, ["score", "--scoring", str(SCORING)])
    assert result.exit_code == 0, result.output
    assert "LLM: 2 ok · ≈$0.20 (оценка по API-ценам)" in result.output
    (run,) = _runs(cli_db, "score")
    assert run.status == "ok" and run.finished_at is not None
    assert run.counts["llm_ok"] == 2
    assert _states(cli_db) == ["scored", "scored"]
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

    _seed(cli_db, jobs=1)
    seen: list[dict[str, Any]] = []

    def fake_score(**kwargs: Any) -> ScoreResult:
        seen.append({**kwargs, "cwd_is_dir": kwargs["cwd"].is_dir()})
        return _ok()

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
    _seed(cli_db, profile=False, jobs=1)
    fake = _FakeScorer(_ok())
    monkeypatch.setattr("aijobradar.cli._make_scorer", fake)
    result = CliRunner().invoke(app, ["score", "--scoring", str(SCORING)])
    assert result.exit_code == 2
    assert NO_PROFILE_MESSAGE in result.output
    assert fake.messages == [] and _runs(cli_db, "score") == []


def test_score_command_with_failures_is_partial(
    cli_db: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(cli_db, jobs=1)
    monkeypatch.setattr("aijobradar.cli._make_scorer", _FakeScorer(_invalid()))
    result = CliRunner().invoke(app, ["score", "--scoring", str(SCORING)])
    assert result.exit_code == 0, result.output
    assert "LLM: 0 ok · 1 invalid_output · первая ошибка: skills_match: x · ≈$0.05" in result.output
    (run,) = _runs(cli_db, "score")
    assert run.status == "partial" and run.counts["llm_invalid_output"] == 1
    assert _states(cli_db) == ["pending_score"]


def test_score_command_respects_limit(cli_db: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed(cli_db, jobs=3)
    monkeypatch.setattr("aijobradar.cli._make_scorer", _FakeScorer(_ok()))
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
    _seed(cli_db, jobs=1)

    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("Fictional Job Text secret")

    monkeypatch.setattr("aijobradar.cli._make_scorer", _FakeScorer(_ok()))
    monkeypatch.setattr("aijobradar.scoring.engine.score_pending", boom)
    result = CliRunner().invoke(app, ["score", "--scoring", str(SCORING)])
    assert result.exit_code == 1
    assert "Оценка прервалась: RuntimeError" in result.output
    assert "Fictional Job Text" not in result.output
    (run,) = _runs(cli_db, "score")
    assert run.status == "failed" and run.finished_at is not None


def _write_run_files(tmp_path: Path) -> list[str]:
    sources = tmp_path / "sources.yaml"
    sources.write_text(
        "himalayas: {enabled: false}\nwwr: {feeds: [remote-full-stack-programming-jobs]}\n"
    )
    profile = tmp_path / "profile.yaml"
    profile.write_text("eligible_places: [PT, EU, EUROPE, EMEA, WORLDWIDE]\n")
    return [
        "run",
        "--config",
        str(sources),
        "--profile",
        str(profile),
        "--rules",
        str(ROOT / "config" / "rules.yaml"),
        "--scoring",
        str(SCORING),
    ]


def _mock_fetch() -> None:
    respx.get(API_URL).mock(
        return_value=httpx.Response(
            200, json=json.loads(fixture_path("jobicy", "engineering.json").read_text())
        )
    )
    respx.get(FEED_URL.format(slug="remote-full-stack-programming-jobs")).mock(
        return_value=httpx.Response(200, content=fixture_path("wwr", "fullstack.rss").read_bytes())
    )


@respx.mock
def test_run_scores_after_fetch_and_failures_make_it_partial(
    cli_db: Engine, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _seed(cli_db)
    _mock_fetch()
    fake = _FakeScorer(_invalid())
    monkeypatch.setattr("aijobradar.cli._make_scorer", fake)
    result = CliRunner().invoke(app, _write_run_files(tmp_path))
    assert result.exit_code == 0, result.output
    assert "Прогон: partial" in result.output
    assert result.output.splitlines()[-1].startswith("LLM: 0 ok · 2 invalid_output")
    (run,) = _runs(cli_db, "fetch")
    assert run.status == "partial"
    assert run.counts["new"] == 4 and run.counts["llm_invalid_output"] == 2
    assert fake.workdir_existed_at_call == [True] * 2
    assert not fake.workdirs[0].exists()


@respx.mock
def test_run_with_all_jobs_scored_stays_ok(
    cli_db: Engine, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _seed(cli_db)
    _mock_fetch()
    monkeypatch.setattr("aijobradar.cli._make_scorer", _FakeScorer(_ok()))
    result = CliRunner().invoke(app, _write_run_files(tmp_path))
    assert result.exit_code == 0, result.output
    assert "Прогон: ok" in result.output
    assert "LLM: 2 ok" in result.output
    assert _runs(cli_db, "fetch")[0].counts["llm_ok"] == 2


@respx.mock
def test_run_keeps_fetch_results_when_scoring_crashes(
    cli_db: Engine, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _seed(cli_db)
    _mock_fetch()

    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("Fictional Job Text secret")

    monkeypatch.setattr("aijobradar.cli._make_scorer", _FakeScorer(_ok()))
    monkeypatch.setattr("aijobradar.scoring.engine.score_pending", boom)
    result = CliRunner().invoke(app, _write_run_files(tmp_path))
    assert result.exit_code == 0, result.output
    assert "Оценка прервалась: RuntimeError" in result.output
    assert "Fictional Job Text" not in result.output
    assert "Прогон: partial" in result.output  # the fetch report is still printed
    assert "LLM:" not in result.output
    (run,) = _runs(cli_db, "fetch")
    assert run.status == "partial" and run.counts["new"] == 4
    assert "Fictional Job Text" not in str(run.counts)
    assert _states(cli_db).count("pending_score") == 2  # fetched and filtered, just not scored


@respx.mock
def test_run_without_profile_in_db_exits_2_before_the_network(
    cli_db: Engine, tmp_path: Path
) -> None:
    result = CliRunner().invoke(app, _write_run_files(tmp_path))  # no routes: any request fails
    assert result.exit_code == 2
    assert NO_PROFILE_MESSAGE in result.output
    assert _runs(cli_db, "fetch") == []


@respx.mock
def test_run_no_score_needs_no_profile_and_never_calls_the_model(
    cli_db: Engine, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _mock_fetch()
    fake = _FakeScorer(_ok())
    monkeypatch.setattr("aijobradar.cli._make_scorer", fake)
    result = CliRunner().invoke(app, [*_write_run_files(tmp_path), "--no-score"])
    assert result.exit_code == 0, result.output
    assert "Прогон: ok" in result.output and "LLM:" not in result.output
    assert fake.messages == [] and fake.workdirs == []
    assert _states(cli_db).count("pending_score") == 2


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
    assert _states(cli_db) == ["new", "rejected"]
    with Session(cli_db) as session:
        by_title = {j.title_raw: j.state for j in session.scalars(select(Job))}
    assert by_title == {"Recheck Engineer old": "new", "Recheck Engineer cur": "rejected"}
