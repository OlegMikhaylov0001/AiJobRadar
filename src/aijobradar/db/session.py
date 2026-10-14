from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url

_CONNECT_TIMEOUT_S = 10


def make_engine(url: str) -> Engine:
    # pre_ping: Neon suspends idle computes; a dead pooled connection must not fail the run.
    # connect_timeout: an unreachable host must fail in seconds, not hang until the job timeout;
    # a value given in the URL itself wins.
    connect_args = (
        {} if "connect_timeout" in make_url(url).query else {"connect_timeout": _CONNECT_TIMEOUT_S}
    )
    return create_engine(url, pool_pre_ping=True, connect_args=connect_args)
