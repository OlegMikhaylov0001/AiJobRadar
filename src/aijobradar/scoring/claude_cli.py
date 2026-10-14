"""The only place that runs `claude -p` (spec §8). Swap this module to change the backend."""

import json
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
    return "; ".join(
        f"{'.'.join(str(p) for p in e['loc']) or '<root>'}: {e['type']}"
        for e in exc.errors(include_input=False)
    )


def _count(value: Any) -> int:
    # The envelope is external input: a malformed number counts as zero, never crashes the run.
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


def _cost(value: Any) -> float:
    try:
        return max(float(value or 0.0), 0.0)
    except (TypeError, ValueError):
        return 0.0


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
    for _attempt in range(2):
        try:
            done = runner(
                cmd,
                input=user_message,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                cwd=cwd,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return ScoreResult(
                ScoreStatus.ERROR, None, None, None, cost, raw, f"timeout after {timeout_s:g}s"
            )
        except FileNotFoundError:
            return ScoreResult(
                ScoreStatus.ERROR, None, None, None, cost, raw, "claude CLI not found"
            )
        except OSError as exc:
            return ScoreResult(
                ScoreStatus.ERROR,
                None,
                None,
                None,
                cost,
                raw,
                f"claude CLI failed: {type(exc).__name__}",
            )
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
        if envelope.get("is_error"):
            return ScoreResult(
                ScoreStatus.ERROR,
                None,
                tokens_in,
                tokens_out,
                cost,
                raw,
                str(envelope.get("subtype") or "is_error")[:100],
            )
        try:
            axes = ScoreAxes.model_validate(envelope.get("structured_output"))
        except ValidationError as exc:
            invalid = _fields(exc)
            continue  # one retry: a malformed answer is often a one-off
        return ScoreResult(ScoreStatus.OK, axes, tokens_in, tokens_out, cost, raw, None)
    return ScoreResult(ScoreStatus.INVALID_OUTPUT, None, tokens_in, tokens_out, cost, raw, invalid)
