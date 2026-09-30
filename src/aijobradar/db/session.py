from sqlalchemy import Engine, create_engine


def make_engine(url: str) -> Engine:
    # pre_ping: Neon suspends idle computes; a dead pooled connection must not fail the run.
    return create_engine(url, pool_pre_ping=True)
