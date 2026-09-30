import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from aijobradar.models import RawJob, SourceResult, SourceStatus

DEGRADED_INVALID_SHARE = 0.2


@dataclass
class Fetched:
    records: list[Any]
    error: str | None = None  # set when part of the fetch failed but some records arrived
    http_status: int | None = None


@dataclass
class FeedRequest:
    label: str
    url: str
    params: dict[str, str | int] | None = field(default=None)


class FeedsFailed(Exception):
    def __init__(self, message: str, http_status: int | None) -> None:
        super().__init__(message)
        self.http_status = http_status


class Adapter(Protocol):
    name: str

    def fetch(self, client: httpx.Client) -> Fetched: ...

    def parse_record(self, record: Any) -> RawJob | None: ...


def describe_error(exc: BaseException) -> tuple[str, int | None]:
    if isinstance(exc, FeedsFailed):
        return str(exc), exc.http_status
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}", exc.response.status_code
    return f"{type(exc).__name__}: {exc}"[:500], None


def fetch_feeds(
    client: httpx.Client,
    requests: Sequence[FeedRequest],
    extract: Callable[[httpx.Response], list[Any]],
    key: Callable[[Any], str],
) -> Fetched:
    """Fetch several feeds of one source; one broken feed degrades, all broken fails."""
    records: list[Any] = []
    seen: set[str] = set()
    errors: list[str] = []
    status: int | None = None
    for req in requests:
        try:
            response = client.get(req.url, params=req.params)
            response.raise_for_status()
            page = extract(response)
        except Exception as exc:  # a feed failure must not abort the other feeds
            message, code = describe_error(exc)
            errors.append(f"{req.label}: {message}")
            status = code or status
            continue
        for record in page:
            record_key = key(record)
            if record_key not in seen:
                seen.add(record_key)
                records.append(record)
    if requests and len(errors) == len(requests):
        raise FeedsFailed("; ".join(errors), status)
    return Fetched(records, error="; ".join(errors) or None, http_status=status)


def run_adapter(adapter: Adapter, client: httpx.Client) -> SourceResult:
    """Run one source end to end. Never raises: every outcome becomes a SourceResult."""
    started = time.monotonic()

    def elapsed() -> int:
        return int((time.monotonic() - started) * 1000)

    try:
        fetched = adapter.fetch(client)
    except Exception as exc:
        message, code = describe_error(exc)
        return SourceResult(
            source=adapter.name,
            status=SourceStatus.FAILED,
            error=message,
            http_status=code,
            duration_ms=elapsed(),
        )

    items: list[RawJob] = []
    invalid = out_of_scope = 0
    first_invalid: str | None = None
    for record in fetched.records:
        try:
            job = adapter.parse_record(record)
        except Exception as exc:  # one bad record must not sink the source
            invalid += 1
            first_invalid = first_invalid or f"{type(exc).__name__}: {exc}"[:300]
            continue
        if job is None:
            out_of_scope += 1
        else:
            items.append(job)

    total = len(fetched.records)
    error = fetched.error
    if fetched.error:
        status = SourceStatus.DEGRADED
    elif total and invalid / total > DEGRADED_INVALID_SHARE:
        status = SourceStatus.DEGRADED
        error = f"{invalid}/{total} invalid; first: {first_invalid}"
    elif not items and not invalid:
        status = SourceStatus.EMPTY
    else:
        status = SourceStatus.OK
    return SourceResult(
        source=adapter.name,
        status=status,
        items=items,
        error=error,
        http_status=fetched.http_status,
        duration_ms=elapsed(),
        invalid_items=invalid,
        out_of_scope=out_of_scope,
    )
