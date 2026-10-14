from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from aijobradar import store
from aijobradar.db.models import ProfileVersion

NOW = datetime(2026, 10, 9, 12, tzinfo=UTC)


def test_first_import_creates_version(session: Session) -> None:
    pv, created = store.add_profile_version(
        session, resume_md="# R", targets_md="# T", note="first", now=NOW
    )
    assert created and pv.id is not None and pv.note == "first"
    assert store.latest_profile_version(session) == pv


def test_same_text_does_not_create_a_new_version(session: Session) -> None:
    first, _ = store.add_profile_version(
        session, resume_md="# R", targets_md="# T", note=None, now=NOW
    )
    again, created = store.add_profile_version(
        session, resume_md="# R", targets_md="# T", note="x", now=NOW + timedelta(hours=1)
    )
    assert not created and again.id == first.id
    assert len(session.scalars(select(ProfileVersion)).all()) == 1


def test_changed_text_creates_new_latest_version(session: Session) -> None:
    store.add_profile_version(session, resume_md="# R", targets_md="# T", note=None, now=NOW)
    pv, created = store.add_profile_version(
        session, resume_md="# R2", targets_md="# T", note=None, now=NOW + timedelta(hours=1)
    )
    assert created and store.latest_profile_version(session) == pv


def test_no_profile_yet(session: Session) -> None:
    assert store.latest_profile_version(session) is None
