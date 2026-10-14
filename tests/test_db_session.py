from typing import Any

import pytest

from aijobradar.db import session as session_module
from aijobradar.db.session import make_engine


def _captured(monkeypatch: pytest.MonkeyPatch, url: str) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    def fake_create_engine(received: str, **kwargs: Any) -> object:
        seen.update(kwargs)
        return object()

    monkeypatch.setattr(session_module, "create_engine", fake_create_engine)
    make_engine(url)
    return seen


def test_engine_pings_and_has_a_connect_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _captured(monkeypatch, "postgresql+psycopg://u:p@host.invalid/db")
    assert seen["pool_pre_ping"] is True
    assert seen["connect_args"] == {"connect_timeout": 10}


def test_connect_timeout_in_the_url_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _captured(monkeypatch, "postgresql+psycopg://u:p@host.invalid/db?connect_timeout=2")
    assert seen["connect_args"] == {}
