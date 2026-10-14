import json
import subprocess
from collections.abc import Callable
from typing import Any

import pytest

from aijobradar.scoring.claude_cli import ScoreStatus, build_command, score
from aijobradar.scoring.schema import score_json_schema
from tests.conftest import fixture_path


def _envelope(name: str) -> str:
    return fixture_path("claude", name).read_text()


def _runner(
    *outputs: str | BaseException, returncode: int = 0
) -> tuple[Callable[..., Any], list[dict[str, Any]]]:
    calls: list[dict[str, Any]] = []
    queue = list(outputs)

    def run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append({"cmd": cmd, **kwargs})
        item = queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        return subprocess.CompletedProcess(cmd, returncode, stdout=item, stderr="secret stderr")

    return run, calls


def _score(runner: Callable[..., Any]) -> Any:
    return score(
        user_message="<job>x</job>", system_prompt="SP", model="m", timeout_s=120, runner=runner
    )


def test_command_disables_tools_and_replaces_system_prompt() -> None:
    cmd = build_command(model="claude-opus-5-5", system_prompt="SP", schema=score_json_schema())
    assert cmd[:2] == ["claude", "-p"]
    joined = " ".join(cmd)
    for flag in (
        "--output-format json",
        "--model claude-opus-5-5",
        "--no-session-persistence",
        "--strict-mcp-config",
        "--disable-slash-commands",
        "--system-prompt SP",
    ):
        assert flag in joined
    assert cmd[cmd.index("--tools") + 1] == ""
    assert cmd[cmd.index("--setting-sources") + 1] == ""
    assert json.loads(cmd[cmd.index("--json-schema") + 1])["additionalProperties"] is False


def test_ok() -> None:
    runner, calls = _runner(_envelope("ok.json"))
    result = _score(runner)
    assert result.status is ScoreStatus.OK and result.axes is not None
    assert result.axes.skills_match == 3
    assert (result.input_tokens, result.output_tokens, result.est_cost_usd) == (5100, 400, 0.12)
    assert calls[0]["input"] == "<job>x</job>" and calls[0]["timeout"] == 120
    assert len(calls) == 1


def test_schema_violation_retries_once_then_invalid() -> None:
    bad = _envelope("schema_violation.json")
    runner, calls = _runner(bad, bad)
    result = _score(runner)
    assert result.status is ScoreStatus.INVALID_OUTPUT and len(calls) == 2
    assert result.est_cost_usd == pytest.approx(0.2)
    assert result.error is not None and "skills_match" in result.error and "9" not in result.error


def test_schema_violation_then_ok_is_ok() -> None:
    runner, _ = _runner(_envelope("schema_violation.json"), _envelope("ok.json"))
    result = _score(runner)
    assert result.status is ScoreStatus.OK and result.est_cost_usd == pytest.approx(0.22)


def test_envelope_not_json_is_error_without_retry() -> None:
    runner, calls = _runner("not json", "still not json")
    result = _score(runner)
    assert result.status is ScoreStatus.ERROR and len(calls) == 1
    assert result.error == "exit 0: output is not JSON"


def test_nonzero_exit_with_non_json_reports_exit_code() -> None:
    runner, calls = _runner("not json", returncode=2)
    result = _score(runner)
    assert result.status is ScoreStatus.ERROR and len(calls) == 1
    assert result.error == "exit 2: output is not JSON"


@pytest.mark.parametrize("stdout", ["[1, 2]", "123", "null"])
def test_non_dict_envelope_is_treated_as_not_json(stdout: str) -> None:
    runner, calls = _runner(stdout, stdout)
    result = _score(runner)
    assert result.status is ScoreStatus.ERROR and len(calls) == 1
    assert result.error == "exit 0: output is not JSON"


def test_refusal() -> None:
    runner, _ = _runner(_envelope("refusal.json"))
    assert _score(runner).status is ScoreStatus.REFUSED


def test_cli_error_hides_text() -> None:
    runner, _ = _runner(_envelope("error.json"))
    result = _score(runner)
    assert result.status is ScoreStatus.ERROR
    assert result.error == "error_during_execution"


def test_timeout() -> None:
    runner, _ = _runner(subprocess.TimeoutExpired(["claude"], 120))
    result = _score(runner)
    assert (result.status, result.error, result.est_cost_usd) == (
        ScoreStatus.ERROR,
        "timeout after 120s",
        0.0,
    )


def test_cli_missing() -> None:
    runner, _ = _runner(FileNotFoundError("claude"))
    assert _score(runner).error == "claude CLI not found"


@pytest.mark.parametrize(
    "outputs",
    [
        ("not json",),
        (_envelope("error.json"),),
        (_envelope("ok.json"),),
        (_envelope("schema_violation.json"), _envelope("schema_violation.json")),
    ],
)
def test_stderr_never_reaches_result(outputs: tuple[str, ...]) -> None:
    runner, _ = _runner(*outputs)
    result = _score(runner)
    assert "secret stderr" not in (result.error or "")
    assert "secret stderr" not in (result.raw_output or "")


def test_malformed_usage_and_cost_do_not_crash() -> None:
    envelope = json.loads(_envelope("ok.json"))
    envelope["usage"] = ["not", "a", "dict"]
    envelope["total_cost_usd"] = "lots"
    runner, _ = _runner(json.dumps(envelope))
    result = _score(runner)
    assert result.status is ScoreStatus.OK
    assert (result.input_tokens, result.output_tokens, result.est_cost_usd) == (0, 0, 0.0)


def test_cli_not_executable_is_error() -> None:
    runner, _ = _runner(PermissionError("claude"))
    result = _score(runner)
    assert (result.status, result.error) == (
        ScoreStatus.ERROR,
        "claude CLI failed: PermissionError",
    )
