from datetime import UTC, datetime
from pathlib import Path

import typer
import yaml
from pydantic import ValidationError

app = typer.Typer(no_args_is_help=True, help="AiJobRadar: personal remote-job monitor.")
db_app = typer.Typer(no_args_is_help=True, help="Database commands.")
app.add_typer(db_app, name="db")


def _validation_details(exc: ValidationError) -> str:
    """'loc: type' pairs only: pydantic messages can echo the offending (personal) value."""
    return "; ".join(
        f"{'.'.join(str(p) for p in e['loc']) or '<root>'}: {e['type']}"
        for e in exc.errors(include_input=False)
    )


@app.command()
def run(
    config: Path = typer.Option(Path("config/sources.yaml"), help="Sources config (YAML)."),
    rules: Path = typer.Option(Path("config/rules.yaml"), help="Rules config (YAML)."),
    profile: Path = typer.Option(
        Path("private/profile.yaml"), help="Candidate profile (YAML, kept out of git)."
    ),
) -> None:
    """Fetch, deduplicate and store jobs, then filter them with deterministic rules."""
    import random

    from sqlalchemy.orm import Session

    from aijobradar.config import Settings, load_config, load_rules_config
    from aijobradar.db.session import make_engine
    from aijobradar.pipeline import RunStatus, format_report, run_fetch
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
    try:
        rules_cfg = load_rules_config(rules)
    except FileNotFoundError:
        typer.echo(f"Ошибка в файле правил {rules}: файл не найден", err=True)
        raise typer.Exit(2) from None
    except ValidationError as exc:
        typer.echo(f"Ошибка в файле правил {rules}: {_validation_details(exc)}", err=True)
        raise typer.Exit(2) from None
    except yaml.YAMLError:
        typer.echo(f"Ошибка в файле правил {rules}: не YAML", err=True)
        raise typer.Exit(2) from None
    try:
        rules_ctx = build_rule_context(rules_cfg, candidate, now)
    except ValueError as exc:
        typer.echo(f"Профиль не согласован с {rules}: {exc}", err=True)
        raise typer.Exit(2) from None

    settings = Settings()
    cfg = load_config(config)
    engine = make_engine(settings.sqlalchemy_url)
    with (
        make_client(settings.user_agent, settings.http_timeout_s) as client,
        Session(engine) as session,
        # A failed run is still committed: its statuses are the evidence. The connection is
        # acquired lazily, so it is first used after run_fetch has finished all network I/O.
        session.begin(),
    ):
        report = run_fetch(
            session,
            build_adapters(cfg),
            client,
            now=now,
            dedup_cfg=cfg.dedup,
            rules_ctx=rules_ctx,
            rng=random.Random(),
        )
    typer.echo(format_report(report))
    if report.status is RunStatus.FAILED:
        raise typer.Exit(1)


@db_app.command("upgrade")
def db_upgrade() -> None:
    """Apply database migrations to DATABASE_URL."""
    from aijobradar.config import Settings
    from aijobradar.db.migrate import upgrade

    upgrade(Settings().sqlalchemy_url)
    typer.echo("Миграции применены.")
