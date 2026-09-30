import typer

app = typer.Typer(no_args_is_help=True, help="AiJobRadar: personal remote-job monitor.")
db_app = typer.Typer(no_args_is_help=True, help="Database commands.")
app.add_typer(db_app, name="db")


@app.command()
def fetch() -> None:
    """Fetch jobs from all enabled sources and store them."""
    raise typer.Exit(2)  # wired up in Task 10


@db_app.command("upgrade")
def db_upgrade() -> None:
    """Apply database migrations."""
    raise typer.Exit(2)  # wired up in Task 8
