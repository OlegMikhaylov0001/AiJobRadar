"""The only place that runs `claude -p` (spec §8). Swap this module to change the backend."""

import json
import math
import os
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from aijobradar.scoring.schema import ScoreAxes, score_json_schema

Runner = Callable[..., subprocess.CompletedProcess[str]]
_MAX_RAW = 50_000
_MAX_ERROR = 300
_IDENTIFIER = re.compile(r"[a-z][a-z0-9_]{0,63}")
# The CLI (>= 2.1.267) validates --json-schema itself and retries internally; these identifiers
# mean it gave up. We must not retry again: that would pay for the same failure twice.
_STRUCTURED_SUBTYPE = "error_max_structured_output_retries"
_STRUCTURED_REASON = "structured_output_retry_exhausted"


class ScoreStatus(StrEnum):
    OK = "ok"
    INVALID_OUTPUT = "invalid_output"
    REFUSED = "refused"
    ERROR = "error"


@dataclass(frozen=True)
class ScoreResult:
    status: ScoreStatus
    axes: ScoreAxes | None
    input_tokens: int | None
    output_tokens: int | None
    est_cost_usd: float
    raw_output: str | None
    error: str | None


def build_command(*, model: str, system_prompt: str, schema: dict[str, Any]) -> list[str]:
    return [
        "claude",
        "-p",
        "--output-format",
        "json",
        "--model",
        model,
        "--tools",
        "",  # no tools: the scorer can have no side effects (spec §7.4)
        "--system-prompt",
        system_prompt,
        "--json-schema",
        json.dumps(schema),
        "--no-session-persistence",
        "--strict-mcp-config",
        "--setting-sources",
        "",
        "--disable-slash-commands",
    ]


def _fields(exc: ValidationError) -> str:
    # Key names in the answer come from the model: only our own field names are stored.
    def where(loc: tuple[int | str, ...]) -> str:
        if not loc:
            return "<root>"
        return str(loc[0]) if loc[0] in ScoreAxes.model_fields else "<extra>"

    return "; ".join(f"{where(e['loc'])}: {e['type']}" for e in exc.errors(include_input=False))[
        :_MAX_ERROR
    ]


def _cli_error(envelope: dict[str, Any]) -> str:
    # CLI-made identifiers only (never `result`, which may echo our input). On API errors the
    # CLI reports subtype "success"; terminal_reason and the HTTP status say what happened.
    reason = str(envelope.get("terminal_reason") or envelope.get("subtype") or "is_error")
    if not _IDENTIFIER.fullmatch(reason):
        reason = "unknown_error"  # fail closed: only identifier-shaped codes are stored
    status = envelope.get("api_error_status")
    return f"{reason} {status}" if isinstance(status, int) else reason


def _structured_retries_exhausted(envelope: dict[str, Any]) -> str | None:
    if envelope.get("subtype") == _STRUCTURED_SUBTYPE:
        return _STRUCTURED_SUBTYPE
    if envelope.get("terminal_reason") == _STRUCTURED_REASON:
        return _STRUCTURED_REASON
    return None


# Variables `claude` has no use for. ANTHROPIC_API_KEY would also switch it from the
# subscription to API billing. A denylist, not an allowlist: see docs/plans/stage1-followups.md.
ENV_DENYLIST = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "DATABASE_URL",
        "PGPASSWORD",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "GH_TOKEN",
        "GITHUB_TOKEN",
    }
)


def _subprocess_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k not in ENV_DENYLIST}


def _count(value: Any) -> int:
    # The envelope is external input: a malformed number counts as zero, never crashes the run.
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError, OverflowError):
        return 0


def _cost(value: Any) -> float:
    try:
        number = float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) and number > 0 else 0.0


def _tokens(usage: Any) -> tuple[int, int]:
    if not isinstance(usage, dict):
        return 0, 0
    inp = sum(
        _count(usage.get(k))
        for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
    )
    return inp, _count(usage.get("output_tokens"))


def score(
    *,
    user_message: str,
    system_prompt: str,
    model: str,
    timeout_s: float,
    runner: Runner = subprocess.run,
    cwd: Path | None = None,
) -> ScoreResult:
    cmd = build_command(model=model, system_prompt=system_prompt, schema=score_json_schema())
    cost, tokens_in, tokens_out = 0.0, 0, 0
    raw: str | None = None
    invalid = ""
    for attempt in range(2):
        failure: str | None = None
        try:
            done = runner(
                cmd,
                input=user_message,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=timeout_s,
                cwd=cwd,
                env=_subprocess_env(),
                check=False,
            )
        except subprocess.TimeoutExpired:
            failure = f"timeout after {timeout_s:g}s"
        except FileNotFoundError:
            failure = "claude CLI not found"
        except OSError as exc:
            failure = f"claude CLI failed: {type(exc).__name__}"
        if failure is not None:
            # A failed retry keeps what the first, paid attempt used.
            used_in, used_out = (tokens_in, tokens_out) if attempt else (None, None)
            return ScoreResult(ScoreStatus.ERROR, None, used_in, used_out, cost, raw, failure)
        raw = done.stdout[:_MAX_RAW]
        try:
            envelope = json.loads(done.stdout)
        except json.JSONDecodeError:
            envelope = None
        if not isinstance(envelope, dict):
            return ScoreResult(
                ScoreStatus.ERROR,
                None,
                None,
                None,
                cost,
                raw,
                f"exit {done.returncode}: output is not JSON",
            )
        cost += _cost(envelope.get("total_cost_usd"))
        i, o = _tokens(envelope.get("usage"))
        tokens_in, tokens_out = tokens_in + i, tokens_out + o
        if envelope.get("stop_reason") == "refusal":
            return ScoreResult(
                ScoreStatus.REFUSED, None, tokens_in, tokens_out, cost, raw, "refusal"
            )
        exhausted = _structured_retries_exhausted(envelope)
        if exhausted is not None:
            return ScoreResult(
                ScoreStatus.INVALID_OUTPUT, None, tokens_in, tokens_out, cost, raw, exhausted
            )
        if envelope.get("is_error"):
            return ScoreResult(
                ScoreStatus.ERROR,
                None,
                tokens_in,
                tokens_out,
                cost,
                raw,
                _cli_error(envelope),
            )
        try:
            axes = ScoreAxes.model_validate(envelope.get("structured_output"))
        except ValidationError as exc:
            invalid = _fields(exc)
            continue  # one retry: a malformed answer is often a one-off
        return ScoreResult(ScoreStatus.OK, axes, tokens_in, tokens_out, cost, raw, None)
    return ScoreResult(ScoreStatus.INVALID_OUTPUT, None, tokens_in, tokens_out, cost, raw, invalid)
