import json
import subprocess
from collections.abc import Callable
from pathlib import Path
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


def test_extra_key_name_from_model_is_not_stored() -> None:
    envelope = json.loads(_envelope("ok.json"))
    envelope["structured_output"]["SECRET job text as a key"] = 1
    runner, _ = _runner(json.dumps(envelope), json.dumps(envelope))
    result = _score(runner)
    assert result.status is ScoreStatus.INVALID_OUTPUT
    assert result.error == "<extra>: extra_forbidden"


def test_timeout_on_retry_keeps_first_attempt_usage() -> None:
    runner, _ = _runner(_envelope("schema_violation.json"), subprocess.TimeoutExpired(["c"], 120))
    result = _score(runner)
    assert result.status is ScoreStatus.ERROR and result.error == "timeout after 120s"
    assert (result.input_tokens, result.output_tokens) == (5100, 400)
    assert result.est_cost_usd == pytest.approx(0.1)


def test_non_finite_numbers_count_as_zero() -> None:
    text = _envelope("ok.json").replace('"total_cost_usd": 0.12', '"total_cost_usd": NaN')
    text = text.replace('"output_tokens": 400', '"output_tokens": Infinity')
    runner, _ = _runner(text)
    result = _score(runner)
    assert result.status is ScoreStatus.OK
    assert (result.output_tokens, result.est_cost_usd) == (0, 0.0)


@pytest.mark.parametrize(
    "code", ["x" * 500, "Ошибка: job text leaked", "has spaces", "API Error: 400 boom", ""]
)
def test_unexpected_cli_codes_are_not_stored(code: str) -> None:
    envelope = json.loads(_envelope("error.json"))
    envelope["subtype"] = code
    runner, _ = _runner(json.dumps(envelope))
    assert _score(runner).error in ("unknown_error", "is_error")


def test_api_error_reports_reason_and_status_not_text() -> None:
    envelope = json.loads(_envelope("error.json"))
    envelope.update(
        subtype="success", terminal_reason="api_error", api_error_status=400, result="job text"
    )
    runner, _ = _runner(json.dumps(envelope))
    result = _score(runner)
    assert (result.status, result.error) == (ScoreStatus.ERROR, "api_error 400")


def test_cli_structured_retries_exhausted_is_invalid_output_without_our_retry() -> None:
    exhausted = _envelope("structured_retries_exhausted.json")
    runner, calls = _runner(exhausted, exhausted)
    result = _score(runner)
    assert len(calls) == 1  # the CLI already retried: a second call would pay twice
    assert result.status is ScoreStatus.INVALID_OUTPUT and result.axes is None
    assert result.error == "error_max_structured_output_retries"
    assert result.est_cost_usd == pytest.approx(0.3)
    assert (result.input_tokens, result.output_tokens) == (15300, 1200)
    assert "job text" not in (result.error or "")


def test_structured_retries_exhausted_detected_by_terminal_reason_alone() -> None:
    envelope = json.loads(_envelope("structured_retries_exhausted.json"))
    envelope["subtype"] = "success"
    runner, calls = _runner(json.dumps(envelope))
    result = _score(runner)
    assert (result.status, len(calls)) == (ScoreStatus.INVALID_OUTPUT, 1)
    assert result.error == "structured_output_retry_exhausted"


def test_structured_retries_exhausted_by_subtype_alone() -> None:
    envelope = json.loads(_envelope("structured_retries_exhausted.json"))
    del envelope["terminal_reason"]
    runner, _ = _runner(json.dumps(envelope))
    assert _score(runner).error == "error_max_structured_output_retries"


def test_retry_after_schema_violation_that_exhausts_cli_retries_keeps_total_cost() -> None:
    runner, calls = _runner(
        _envelope("schema_violation.json"), _envelope("structured_retries_exhausted.json")
    )
    result = _score(runner)
    assert len(calls) == 2 and result.status is ScoreStatus.INVALID_OUTPUT
    assert result.est_cost_usd == pytest.approx(0.4)


def test_subprocess_env_drops_anthropic_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-fictional")
    monkeypatch.setenv("HOME", "/home/fictional")
    runner, calls = _runner(_envelope("ok.json"))
    _score(runner)
    env = calls[0]["env"]
    assert "ANTHROPIC_API_KEY" not in env
    assert env["HOME"] == "/home/fictional" and "PATH" in env


def test_subprocess_env_is_computed_at_call_time(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("AIJOBRADAR_TEST_MARK", "first")
    runner, calls = _runner(_envelope("ok.json"), _envelope("ok.json"))
    _score(runner)
    monkeypatch.setenv("AIJOBRADAR_TEST_MARK", "second")
    _score(runner)
    assert [c["env"]["AIJOBRADAR_TEST_MARK"] for c in calls] == ["first", "second"]


def test_cwd_is_passed_to_the_subprocess(tmp_path: Path) -> None:
    runner, calls = _runner(_envelope("ok.json"))
    score(
        user_message="<job>x</job>",
        system_prompt="SP",
        model="m",
        timeout_s=120,
        runner=runner,
        cwd=tmp_path,
    )
    assert calls[0]["cwd"] == tmp_path


def test_subprocess_text_is_decoded_as_utf8() -> None:
    runner, calls = _runner(_envelope("ok.json"))
    _score(runner)
    assert calls[0]["text"] is True and calls[0]["encoding"] == "utf-8"
