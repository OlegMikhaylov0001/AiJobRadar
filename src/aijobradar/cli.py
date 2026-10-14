import os
import uuid
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import typer
import yaml
from pydantic import ValidationError

if TYPE_CHECKING:
    from sqlalchemy import Engine

    from aijobradar.config import RulesConfig, ScoringConfig, Settings
    from aijobradar.pipeline import FetchReport
    from aijobradar.scoring.engine import Scorer, ScoringReport
    from aijobradar.telegram.client import TelegramClient

# Locals in pretty tracebacks could print the Telegram URL, token included.
app = typer.Typer(
    no_args_is_help=True,
    help="AiJobRadar: personal remote-job monitor.",
    pretty_exceptions_show_locals=False,
)
db_app = typer.Typer(no_args_is_help=True, help="Database commands.")
app.add_typer(db_app, name="db")
profile_app = typer.Typer(no_args_is_help=True, help="Candidate profile (resume and targets).")
app.add_typer(profile_app, name="profile")
rules_app = typer.Typer(no_args_is_help=True, help="Deterministic pre-LLM rules.")
app.add_typer(rules_app, name="rules")


def _validation_details(exc: ValidationError, *, own_messages: bool = False) -> str:
    """'loc: type' pairs only: pydantic messages can echo the offending (personal) value.

    `own_messages` is for public configs: a `value_error` raised by our own validator is shown
    with its message (it names a key, e.g. a mistyped flag, never a personal value).
    """

    def detail(e: Any) -> str:
        where = ".".join(str(p) for p in e["loc"]) or "<root>"
        if own_messages and e["type"] == "value_error":
            message = str(e["msg"]).removeprefix("Value error, ")
            return f"{where}: {message}" if e["loc"] else message
        return f"{where}: {e['type']}"

    return "; ".join(detail(e) for e in exc.errors(include_input=False))


_NO_PROFILE_IN_DB = (
    "Нет профиля в БД. Импортируйте: aijobradar profile import private/resume.md private/targets.md"
)


def _load_scoring(scoring_path: Path) -> "tuple[ScoringConfig, str]":
    """Scoring config and its system prompt. Errors name files and fields, never contents."""
    from aijobradar.config import load_scoring_config
    from aijobradar.scoring.prompt import load_system_prompt

    try:
        cfg = load_scoring_config(scoring_path)
    except FileNotFoundError:
        typer.echo(f"Ошибка в файле оценки {scoring_path}: файл не найден", err=True)
        raise typer.Exit(2) from None
    except ValidationError as exc:
        details = _validation_details(exc, own_messages=True)
        typer.echo(f"Ошибка в файле оценки {scoring_path}: {details}", err=True)
        raise typer.Exit(2) from None
    except yaml.YAMLError:
        typer.echo(f"Ошибка в файле оценки {scoring_path}: не YAML", err=True)
        raise typer.Exit(2) from None
    except (OSError, UnicodeDecodeError):
        typer.echo(f"Ошибка в файле оценки {scoring_path}: не удалось прочитать", err=True)
        raise typer.Exit(2) from None
    prompt_path = scoring_path.parent / "prompts" / f"{cfg.prompt_version}.md"
    try:
        prompt = load_system_prompt(scoring_path.parent / "prompts", cfg.prompt_version)
    except FileNotFoundError:
        typer.echo(f"Нет файла системного промпта {prompt_path}", err=True)
        raise typer.Exit(2) from None
    except (OSError, UnicodeDecodeError):
        typer.echo(f"Не удалось прочитать системный промпт {prompt_path}", err=True)
        raise typer.Exit(2) from None
    return cfg, prompt


def _load_rules_cfg(rules: Path) -> "RulesConfig":
    """Rules config; errors name the file and fields, never contents."""
    from aijobradar.config import load_rules_config

    try:
        return load_rules_config(rules)
    except FileNotFoundError:
        typer.echo(f"Ошибка в файле правил {rules}: файл не найден", err=True)
        raise typer.Exit(2) from None
    except ValidationError as exc:
        typer.echo(f"Ошибка в файле правил {rules}: {_validation_details(exc)}", err=True)
        raise typer.Exit(2) from None
    except yaml.YAMLError:
        typer.echo(f"Ошибка в файле правил {rules}: не YAML", err=True)
        raise typer.Exit(2) from None


def _latest_profile_id(engine: "Engine") -> int:
    """Only the id crosses sessions: scoring re-loads the version in its own session."""
    from sqlalchemy.orm import Session

    from aijobradar import store

    with Session(engine) as session:
        version = store.latest_profile_version(session)
        if version is None:
            typer.echo(_NO_PROFILE_IN_DB, err=True)
            raise typer.Exit(2)
        return version.id


def _make_scorer(cfg: "ScoringConfig", system_prompt: str, workdir: Path) -> "Scorer":
    """The one place the real `claude` is wired in; tests replace it."""
    from aijobradar.scoring import claude_cli
    from aijobradar.scoring.claude_cli import ScoreResult

    def scorer(message: str) -> ScoreResult:
        return claude_cli.score(
            user_message=message,
            system_prompt=system_prompt,
            model=cfg.model,
            timeout_s=cfg.timeout_s,
            cwd=workdir,
        )

    return scorer


def _score_jobs(
    engine: "Engine",
    *,
    run_id: uuid.UUID,
    profile_id: int,
    cfg: "ScoringConfig",
    system_prompt: str,
    limit: int | None,
) -> "ScoringReport":
    import tempfile

    from sqlalchemy.orm import Session

    from aijobradar.db.models import ProfileVersion
    from aijobradar.scoring.engine import score_pending

    # `claude` runs in an empty scratch directory so it cannot pick up the project's CLAUDE.md.
    with tempfile.TemporaryDirectory() as workdir, Session(engine) as session:
        profile = session.get(ProfileVersion, profile_id)
        if profile is None:
            raise LookupError("profile version not found")
        return score_pending(
            session,
            run_id=run_id,
            profile=profile,
            cfg=cfg,
            scorer=_make_scorer(cfg, system_prompt, Path(workdir)),
            now=datetime.now(UTC),
            limit=limit,
        )


def _score_after_fetch(
    engine: "Engine",
    report: "FetchReport",
    *,
    profile_id: int,
    cfg: "ScoringConfig",
    system_prompt: str,
    limit: int | None,
) -> "FetchReport":
    """Fetch and rules are already committed: a scoring crash must not lose them."""
    import dataclasses

    from sqlalchemy.orm import Session

    from aijobradar import store
    from aijobradar.pipeline import RunStatus, apply_scoring_result, scoring_counts

    try:
        scoring = _score_jobs(
            engine,
            run_id=report.run_id,
            profile_id=profile_id,
            cfg=cfg,
            system_prompt=system_prompt,
            limit=limit,
        )
        report = apply_scoring_result(report, scoring)
        extra = scoring_counts(scoring)
    except Exception as exc:
        # Class name only: the message may carry job text.
        typer.echo(f"Оценка прервалась: {type(exc).__name__}", err=True)
        status = RunStatus.PARTIAL if report.status is RunStatus.OK else report.status
        report, extra = dataclasses.replace(report, status=status), {}
    try:
        with Session(engine) as session, session.begin():
            store.update_run_result(
                session, report.run_id, status=report.status.value, extra_counts=extra
            )
    except Exception as exc:
        typer.echo(f"Не удалось записать итог оценки: {type(exc).__name__}", err=True)
    return report


_LOCK_ID = 4_204_2026  # one AiJobRadar run (run/score/labels-poll) per database at a time


def _settings() -> "Settings":
    from aijobradar.config import Settings

    try:
        return Settings()
    except ValidationError:
        # Field names only: a validation message could echo a secret value.
        typer.echo("Нет DATABASE_URL: задайте переменную окружения или .env.", err=True)
        raise typer.Exit(2) from None


@contextmanager
def _run_lock(engine: "Engine") -> Iterator[None]:
    from sqlalchemy import func, select

    with engine.connect() as conn:
        acquired = conn.execute(select(func.pg_try_advisory_lock(_LOCK_ID))).scalar()
        conn.commit()  # the session-level lock survives the commit; no transaction stays open
        if not acquired:
            typer.echo("Другой прогон уже идёт.", err=True)
            raise typer.Exit(1)
        try:
            yield
        finally:
            conn.execute(select(func.pg_advisory_unlock(_LOCK_ID)))
            conn.commit()


def _write_step_summary(text: str) -> None:
    """Public Actions summary: the report holds statuses and counters, never job text."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"```\n{text}\n```\n")


@dataclass(frozen=True)
class _Digest:
    """Where and how the daily digest goes out."""

    client: "TelegramClient"
    chat_id: str
    cfg: "ScoringConfig"


@contextmanager
def _telegram_client(token: str, timeout_s: float) -> "Iterator[TelegramClient]":
    import httpx

    from aijobradar.telegram.client import TelegramClient

    with httpx.Client(timeout=timeout_s) as http:
        yield TelegramClient(token=token, http=http)


def _database_reachable(engine: "Engine") -> bool:
    from sqlalchemy.exc import OperationalError

    try:
        with engine.connect():
            return True
    except OperationalError:
        return False


def _send_crash(digest: _Digest, *, reason: str, report_text: str | None) -> None:
    from aijobradar.telegram.client import TelegramError
    from aijobradar.telegram.deliver import send_crash

    try:
        send_crash(digest.client, chat_id=digest.chat_id, reason=reason, report_text=report_text)
    except TelegramError as exc:
        typer.echo(f"Telegram не принял: {exc}", err=True)


def _record_run_status(engine: "Engine", report: "FetchReport", status: str) -> None:
    from sqlalchemy.orm import Session

    from aijobradar import store

    try:
        with Session(engine) as session, session.begin():
            store.update_run_result(session, report.run_id, status=status, extra_counts={})
    except Exception as exc:
        typer.echo(f"Не удалось записать итог доставки: {type(exc).__name__}", err=True)


def _deliver_digest(engine: "Engine", digest: _Digest, report: "FetchReport") -> "FetchReport":
    """Send the digest and record it; a refusal or crash fails the run (exit 1)."""
    import dataclasses
    import random

    from sqlalchemy.orm import Session

    from aijobradar import store
    from aijobradar.pipeline import RunStatus, format_report
    from aijobradar.select import select_digest
    from aijobradar.telegram.client import TelegramError
    from aijobradar.telegram.deliver import deliver_digest

    now = datetime.now(UTC)
    try:
        # A session of its own: deliver_digest commits per delivery and rolls back a failed send.
        with Session(engine, expire_on_commit=False) as session:
            picks = select_digest(session, cfg=digest.cfg, now=now, rng=random.Random())
            delivery = deliver_digest(
                session,
                digest.client,
                chat_id=digest.chat_id,
                run_id=report.run_id,
                picks=picks,
                report_text=format_report(report),
                now=now,
            )
        report = dataclasses.replace(report, delivery=delivery)
        with Session(engine) as session, session.begin():
            store.update_run_result(
                session,
                report.run_id,
                status=report.status.value,
                extra_counts={
                    "delivered_top": delivery.top,
                    "delivered_explore": delivery.explore,
                },
            )
    except Exception as exc:
        if isinstance(exc, TelegramError):
            typer.echo(f"Telegram не принял: {exc}", err=True)
        else:
            # Class name only: the message may carry job text.
            typer.echo(f"Выдача прервалась: {type(exc).__name__}", err=True)
        failed = dataclasses.replace(report, status=RunStatus.FAILED, delivery=None)
        _record_run_status(engine, failed, RunStatus.FAILED.value)
        typer.echo(format_report(failed))
        _write_step_summary(format_report(failed))
        raise typer.Exit(1) from None
    return report


@app.command()
def run(
    config: Path = typer.Option(Path("config/sources.yaml"), help="Sources config (YAML)."),
    rules: Path = typer.Option(Path("config/rules.yaml"), help="Rules config (YAML)."),
    profile: Path = typer.Option(
        Path("private/profile.yaml"), help="Candidate profile (YAML, kept out of git)."
    ),
    score: bool = typer.Option(True, "--score/--no-score", help="Score jobs with the LLM."),
    scoring: Path = typer.Option(Path("config/scoring.yaml"), help="Scoring config (YAML)."),
    limit: int | None = typer.Option(
        None, min=1, help="At most N model calls this run (identical postings share one)."
    ),
    deliver: bool = typer.Option(
        False, "--deliver/--no-deliver", help="Send the digest to Telegram."
    ),
) -> None:
    """Fetch, deduplicate and store jobs, filter them with rules, score them with the LLM and
    optionally send the digest to Telegram."""
    import random

    from sqlalchemy.orm import Session

    from aijobradar import store
    from aijobradar.config import load_config
    from aijobradar.db.session import make_engine
    from aijobradar.pipeline import RunStatus, format_report, himalayas_lookback, run_fetch
    from aijobradar.profile import ProfileMissing, load_profile
    from aijobradar.rules.context import build_rule_context
    from aijobradar.sources import build_adapters
    from aijobradar.sources.common import make_client

    now = datetime.now(UTC)
    # Profile and rules are checked before anything touches the network or the database.
    try:
        candidate = load_profile(profile)
    except ValidationError as exc:
        typer.echo(f"Профиль {profile} некорректен: {_validation_details(exc)}", err=True)
        raise typer.Exit(2) from None
    except yaml.YAMLError:
        typer.echo(f"Профиль {profile} некорректен: не YAML", err=True)
        raise typer.Exit(2) from None
    except ProfileMissing:
        typer.echo(
            f"Нет файла профиля {profile}. "
            f"Скопируйте config/profile.example.yaml в {profile} и заполните.",
            err=True,
        )
        raise typer.Exit(2) from None
    rules_cfg = _load_rules_cfg(rules)
    try:
        rules_ctx = build_rule_context(rules_cfg, candidate, now)
    except ValueError as exc:
        typer.echo(f"Профиль не согласован с {rules}: {exc}", err=True)
        raise typer.Exit(2) from None
    scoring_cfg, system_prompt = _load_scoring(scoring) if score else (None, "")
    # The digest needs only the config (threshold, sizes), even when this run does not score.
    digest_cfg = (scoring_cfg or _load_scoring(scoring)[0]) if deliver else None

    settings = _settings()
    token, chat_id = settings.telegram_bot_token, settings.telegram_chat_id
    if deliver and not (token and chat_id):
        typer.echo("Нет TELEGRAM_BOT_TOKEN или TELEGRAM_CHAT_ID.", err=True)
        raise typer.Exit(2)
    cfg = load_config(config)
    engine = make_engine(settings.sqlalchemy_url)
    with ExitStack() as stack:
        digest: _Digest | None = None
        if deliver and token and chat_id and digest_cfg:
            telegram = stack.enter_context(_telegram_client(token, settings.http_timeout_s))
            digest = _Digest(telegram, chat_id, digest_cfg)
        if not _database_reachable(engine):
            if digest is not None:
                _send_crash(
                    digest,
                    reason="БД недоступна — без неё нельзя исключить повторы",
                    report_text=None,
                )
            typer.echo("БД недоступна.", err=True)
            raise typer.Exit(1)
        with _run_lock(engine):
            # The database profile version is checked before the network, like the files above.
            profile_id = _latest_profile_id(engine) if score else None
            # Himalayas walks back to its last successful run, so a late or missed cron loses
            # nothing; computed before any network call.
            lookback: int | None = None
            if cfg.himalayas.enabled:
                with Session(engine) as session:
                    last_ok = store.last_source_success(session, "himalayas")
                lookback = himalayas_lookback(
                    last_ok,
                    now,
                    base_hours=cfg.himalayas.lookback_hours,
                    max_hours=cfg.himalayas.max_lookback_hours,
                )
            with (
                make_client(settings.user_agent, settings.http_timeout_s) as client,
                Session(engine) as session,
                # A failed run is still committed: its statuses are the evidence. The connection
                # is acquired lazily, so it is first used after run_fetch has finished all
                # network I/O.
                session.begin(),
            ):
                report = run_fetch(
                    session,
                    build_adapters(cfg, himalayas_lookback_hours=lookback),
                    client,
                    now=now,
                    dedup_cfg=cfg.dedup,
                    rules_ctx=rules_ctx,
                    rng=random.Random(),
                )
            # The fetch session has committed: scoring gets its own, and may fail without
            # losing it.
            if scoring_cfg is not None and profile_id is not None:
                report = _score_after_fetch(
                    engine,
                    report,
                    profile_id=profile_id,
                    cfg=scoring_cfg,
                    system_prompt=system_prompt,
                    limit=limit,
                )
            if digest is not None:
                if report.status is RunStatus.FAILED:
                    _send_crash(
                        digest, reason="Все источники недоступны", report_text=format_report(report)
                    )
                else:
                    report = _deliver_digest(engine, digest, report)
    typer.echo(format_report(report))
    _write_step_summary(format_report(report))
    if report.status is RunStatus.FAILED:
        raise typer.Exit(1)


@app.command()
def score(
    scoring: Path = typer.Option(Path("config/scoring.yaml"), help="Scoring config (YAML)."),
    limit: int | None = typer.Option(
        None, min=1, help="At most N model calls this run (identical postings share one)."
    ),
) -> None:
    """Score jobs waiting in pending_score with the LLM (no fetching)."""
    from sqlalchemy.orm import Session

    from aijobradar import store
    from aijobradar.db.models import Run
    from aijobradar.db.session import make_engine
    from aijobradar.pipeline import format_scoring_line, scoring_counts

    cfg, system_prompt = _load_scoring(scoring)
    engine = make_engine(_settings().sqlalchemy_url)
    with _run_lock(engine):
        profile_id = _latest_profile_id(engine)
        with Session(engine) as session, session.begin():
            run_id = store.start_run(session, "score", datetime.now(UTC)).id
        try:
            report = _score_jobs(
                engine,
                run_id=run_id,
                profile_id=profile_id,
                cfg=cfg,
                system_prompt=system_prompt,
                limit=limit,
            )
        except Exception as exc:
            typer.echo(f"Оценка прервалась: {type(exc).__name__}", err=True)
            with Session(engine) as session, session.begin():
                run_row = session.get(Run, run_id)
                if run_row is not None:
                    store.finish_run(session, run_row, "failed", {}, datetime.now(UTC))
            raise typer.Exit(1) from None
        status = "partial" if report.failed else "ok"
        with Session(engine) as session, session.begin():
            run_row = session.get(Run, run_id)
            if run_row is not None:
                store.finish_run(
                    session, run_row, status, scoring_counts(report), datetime.now(UTC)
                )
    typer.echo(format_scoring_line(report))


@rules_app.command("recheck")
def rules_recheck(
    rules: Path = typer.Option(Path("config/rules.yaml"), help="Rules config (YAML)."),
) -> None:
    """Send jobs rejected under another rules version back for a fresh rules check."""
    from sqlalchemy.orm import Session

    from aijobradar import store
    from aijobradar.db.session import make_engine

    # Only the version is needed: no personal profile file is read.
    version = _load_rules_cfg(rules).version
    engine = make_engine(_settings().sqlalchemy_url)
    with Session(engine) as session, session.begin():
        reopened = store.reopen_rejected_from_other_versions(session, version)
    typer.echo(f"Вернул на проверку правилами: {reopened}.")


@app.command("labels-poll")
def labels_poll() -> None:
    """Collect 👍/👎 presses from Telegram into the labels table."""
    from sqlalchemy.orm import Session

    from aijobradar import store
    from aijobradar.db.session import make_engine
    from aijobradar.telegram.client import TelegramError
    from aijobradar.telegram.labels import poll_labels

    settings = _settings()
    token, chat_id = settings.telegram_bot_token, settings.telegram_chat_id
    if not (token and chat_id):
        typer.echo("Нет TELEGRAM_BOT_TOKEN или TELEGRAM_CHAT_ID.", err=True)
        raise typer.Exit(2)
    engine = make_engine(settings.sqlalchemy_url)
    with (
        _run_lock(engine),
        _telegram_client(token, settings.http_timeout_s) as client,
        Session(engine) as session,
    ):
        run = store.start_run(session, "labels", datetime.now(UTC))
        session.commit()
        try:
            report = poll_labels(session, client, chat_id=chat_id, now=datetime.now(UTC))
        except Exception as exc:
            session.rollback()
            if isinstance(exc, TelegramError):
                typer.echo(f"Telegram не принял: {exc}", err=True)
            else:
                # Class name only: the message may carry job text.
                typer.echo(f"Метки прервались: {type(exc).__name__}", err=True)
            store.finish_run(session, run, "failed", {}, datetime.now(UTC))
            session.commit()
            raise typer.Exit(1) from None
        counts = {"updates": report.updates, "labeled": report.labeled, "ignored": report.ignored}
        store.finish_run(session, run, "ok", counts, datetime.now(UTC))
        session.commit()
    typer.echo(
        f"Метки: получено {report.updates}, записано {report.labeled}, пропущено {report.ignored}."
    )


@db_app.command("upgrade")
def db_upgrade() -> None:
    """Apply database migrations to DATABASE_URL."""
    from aijobradar.db.migrate import upgrade

    upgrade(_settings().sqlalchemy_url)
    typer.echo("Миграции применены.")


def _read_profile_doc(path: Path) -> str:
    """Contents are personal: errors name the path only."""
    if not path.is_file():
        typer.echo(f"Нет файла {path}.", err=True)
        raise typer.Exit(2)
    try:
        text = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        typer.echo(f"Не удалось прочитать {path}.", err=True)
        raise typer.Exit(2) from None
    if not text:
        typer.echo(f"Файл {path} пуст.", err=True)
        raise typer.Exit(2)
    return text


@profile_app.command("import")
def profile_import(
    resume: Path = typer.Argument(..., help="Resume facts (Markdown, kept out of git)."),
    targets: Path = typer.Argument(..., help="Targets and constraints (Markdown, out of git)."),
    note: str | None = typer.Option(None, help="Short note for this version."),
) -> None:
    """Store the resume and targets as a new profile version (only if the text changed)."""
    from sqlalchemy.orm import Session

    from aijobradar import store
    from aijobradar.db.session import make_engine

    resume_md, targets_md = _read_profile_doc(resume), _read_profile_doc(targets)
    engine = make_engine(_settings().sqlalchemy_url)
    with Session(engine) as session, session.begin():
        version, created = store.add_profile_version(
            session, resume_md=resume_md, targets_md=targets_md, note=note, now=datetime.now(UTC)
        )
        version_id = version.id
    if created:
        typer.echo(f"Профиль сохранён: версия {version_id}.")
    else:
        typer.echo(f"Профиль не изменился: версия {version_id}.")
