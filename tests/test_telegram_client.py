import httpx
import pytest
import respx

from aijobradar.telegram.client import TelegramClient, TelegramError

TOKEN = "123:TEST-SECRET"
BASE = f"https://api.telegram.org/bot{TOKEN}"


def _client(sleeps: list[float]) -> TelegramClient:
    return TelegramClient(token=TOKEN, http=httpx.Client(), sleep=sleeps.append)


@respx.mock
def test_send_message_returns_message_id_and_uses_html() -> None:
    route = respx.post(f"{BASE}/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"message_id": 77}})
    )
    assert _client([]).send_message("42", "<b>hi</b>", {"inline_keyboard": []}) == 77
    body = route.calls[0].request.read().decode()
    assert '"parse_mode":"HTML"' in body.replace(" ", "") and '"chat_id":"42"' in body.replace(
        " ", ""
    )


@respx.mock
def test_429_waits_retry_after_then_succeeds() -> None:
    respx.post(f"{BASE}/sendMessage").mock(
        side_effect=[
            httpx.Response(
                429, json={"ok": False, "error_code": 429, "parameters": {"retry_after": 3}}
            ),
            httpx.Response(200, json={"ok": True, "result": {"message_id": 1}}),
        ]
    )
    sleeps: list[float] = []
    assert _client(sleeps).send_message("42", "x") == 1
    assert sleeps == [3]


@respx.mock
def test_network_errors_retry_three_times_then_fail_without_token() -> None:
    respx.post(f"{BASE}/sendMessage").mock(side_effect=httpx.ConnectError("boom " + BASE))
    sleeps: list[float] = []
    with pytest.raises(TelegramError) as info:
        _client(sleeps).send_message("42", "x")
    assert len(sleeps) == 2  # 3 attempts, 2 waits
    assert TOKEN not in str(info.value) and "api.telegram.org" not in str(info.value)
    assert str(info.value) == "sendMessage: ConnectError"


@respx.mock
def test_bad_request_is_not_retried_and_hides_description() -> None:
    respx.post(f"{BASE}/sendMessage").mock(
        return_value=httpx.Response(
            400,
            json={"ok": False, "error_code": 400, "description": "can't parse: SECRET JOB TEXT"},
        )
    )
    sleeps: list[float] = []
    with pytest.raises(TelegramError) as info:
        _client(sleeps).send_message("42", "x")
    assert sleeps == [] and str(info.value) == "sendMessage: HTTP 400"
    assert "SECRET JOB TEXT" not in repr(info.value)


@respx.mock
def test_error_chain_does_not_leak_token() -> None:
    respx.post(f"{BASE}/sendMessage").mock(side_effect=httpx.ConnectError("boom " + BASE))
    with pytest.raises(TelegramError) as info:
        _client([]).send_message("42", "x")
    err = info.value
    assert err.__suppress_context__ is True
    for part in (err, err.__cause__, err.__context__):
        assert TOKEN not in repr(part) and TOKEN not in str(part)


def test_client_repr_does_not_show_token() -> None:
    assert TOKEN not in repr(_client([]))


@respx.mock
def test_get_updates_and_answer_callback() -> None:
    respx.post(f"{BASE}/getUpdates").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": [{"update_id": 5}]})
    )
    answer = respx.post(f"{BASE}/answerCallbackQuery").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": True})
    )
    client = _client([])
    assert client.get_updates(5) == [{"update_id": 5}]
    client.answer_callback("cb-1", "Записал")
    assert answer.called
