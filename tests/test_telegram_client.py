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


@respx.mock
def test_5xx_is_retried_then_succeeds() -> None:
    respx.post(f"{BASE}/sendMessage").mock(
        side_effect=[
            httpx.Response(500, json={"ok": False}),
            httpx.Response(200, json={"ok": True, "result": {"message_id": 9}}),
        ]
    )
    sleeps: list[float] = []
    assert _client(sleeps).send_message("42", "x") == 9
    assert sleeps == [1.0]


@respx.mock
def test_5xx_exhaustion_fails_after_three_attempts() -> None:
    route = respx.post(f"{BASE}/sendMessage").mock(
        side_effect=[httpx.Response(502, json={"ok": False})] * 3
    )
    sleeps: list[float] = []
    with pytest.raises(TelegramError) as info:
        _client(sleeps).send_message("42", "x")
    assert route.call_count == 3 and len(sleeps) == 2
    assert str(info.value) == "sendMessage: HTTP 502"


@pytest.mark.parametrize(("retry_after", "expected"), [(-5, 0.0), (10_000, 60.0)])
@respx.mock
def test_retry_after_is_clamped(retry_after: int, expected: float) -> None:
    respx.post(f"{BASE}/sendMessage").mock(
        side_effect=[
            httpx.Response(429, json={"ok": False, "parameters": {"retry_after": retry_after}}),
            httpx.Response(200, json={"ok": True, "result": {"message_id": 1}}),
        ]
    )
    sleeps: list[float] = []
    assert _client(sleeps).send_message("42", "x") == 1
    assert sleeps == [expected]


@pytest.mark.parametrize(
    "result", [None, {}, {"message_id": True}, {"message_id": "77"}, 5], ids=repr
)
@respx.mock
def test_send_message_rejects_bad_result_shape(result: object) -> None:
    respx.post(f"{BASE}/sendMessage").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": result})
    )
    with pytest.raises(TelegramError) as info:
        _client([]).send_message("42", "x")
    assert str(info.value) == "sendMessage: bad response"


@pytest.mark.parametrize("result", [5, {}, None], ids=repr)
@respx.mock
def test_get_updates_rejects_non_list_result(result: object) -> None:
    respx.post(f"{BASE}/getUpdates").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": result})
    )
    with pytest.raises(TelegramError) as info:
        _client([]).get_updates(0)
    assert str(info.value) == "getUpdates: bad response"


@pytest.mark.parametrize("body", [[1], "x"], ids=repr)
@respx.mock
def test_non_object_json_body_is_telegram_error(body: object) -> None:
    respx.post(f"{BASE}/sendMessage").mock(return_value=httpx.Response(200, json=body))
    with pytest.raises(TelegramError) as info:
        _client([]).send_message("42", "x")
    assert str(info.value) == "sendMessage: HTTP 200"


@respx.mock
def test_invalid_url_is_telegram_error_without_token() -> None:
    respx.post(f"{BASE}/sendMessage").mock(side_effect=httpx.InvalidURL("bad " + BASE))
    with pytest.raises(TelegramError) as info:
        _client([]).send_message("42", "x")
    assert str(info.value) == "sendMessage: InvalidURL"
    assert TOKEN not in repr(info.value) and info.value.__cause__ is None


def test_any_exception_from_the_http_layer_is_reduced_to_its_class_name() -> None:
    class Stub:
        def post(self, url: str, **kwargs: object) -> httpx.Response:
            raise RuntimeError(f"unexpected: {url}")

    client = TelegramClient(token=TOKEN, http=Stub(), sleep=lambda _: None)  # type: ignore[arg-type]
    with pytest.raises(TelegramError) as info:
        client.send_message("42", "x")
    assert str(info.value) == "sendMessage: RuntimeError"
    assert TOKEN not in repr(info.value) and info.value.__cause__ is None
    assert info.value.__suppress_context__ or info.value.__context__ is None
