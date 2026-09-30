from datetime import UTC, datetime
from pathlib import Path

import typer

app = typer.Typer(no_args_is_help=True, help="AiJobRadar: personal remote-job monitor.")
db_app = typer.Typer(no_args_is_help=True, help="Database commands.")
app.add_typer(db_app, name="db")


@app.command()
def fetch(
    config: Path = typer.Option(Path("config/sources.yaml"), help="Sources config (YAML)."),
) -> None:
    """Fetch jobs from all enabled sources, deduplicate and store them."""
    from sqlalchemy.orm import Session

    from aijobradar.config import Settings, load_config
    from aijobradar.db.session import make_engine
    from aijobradar.pipeline import RunStatus, format_report, run_fetch
    from aijobradar.sources import build_adapters
    from aijobradar.sources.common import make_client

    settings = Settings()
    cfg = load_config(config)
    engine = make_engine(settings.sqlalchemy_url)
    with (
        make_client(settings.user_agent, settings.http_timeout_s) as client,
        Session(engine) as session,
        session.begin(),  # a failed run is still committed: its statuses are the evidence
    ):
        report = run_fetch(
            session, build_adapters(cfg), client, now=datetime.now(UTC), dedup_cfg=cfg.dedup
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
