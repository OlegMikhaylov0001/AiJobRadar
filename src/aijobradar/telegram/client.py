"""The only place that talks to the Telegram Bot API. The token is part of every URL, so no
error raised here carries a URL, an httpx message or Telegram's own description."""

import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

_API = "https://api.telegram.org"
# Telegram's retry_after is advisory; a huge value would stall the run, a negative one is invalid.
_MAX_RETRY_AFTER = 60.0


# Errors raised before the request left this process: Telegram cannot have acted on it.
_NOT_SENT = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout, httpx.InvalidURL)
# Repeating these could post a message twice: there is no idempotency key in the Bot API.
_NOT_IDEMPOTENT = frozenset({"sendMessage"})


class TelegramError(Exception):
    """`ambiguous`: Telegram may have acted on the request (the answer was lost or unreadable),
    so the caller must not assume it failed."""

    def __init__(self, message: str, *, ambiguous: bool = False) -> None:
        super().__init__(message)
        self.ambiguous = ambiguous


def _json_object(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _retry_delay(retry_after: object, fallback: float) -> float:
    if isinstance(retry_after, bool) or not isinstance(retry_after, int | float):
        return fallback
    if not math.isfinite(retry_after):
        return fallback
    return max(0.0, min(float(retry_after), _MAX_RETRY_AFTER))


@dataclass
class TelegramClient:
    token: str = field(repr=False)
    http: httpx.Client
    sleep: Callable[[float], None] = field(default=time.sleep)
    attempts: int = 3

    def _post(
        self, method: str, payload: dict[str, Any]
    ) -> tuple[httpx.Response | None, str, bool]:
        """(response, error class name, whether the failed request may have reached Telegram).

        The URL is built here, not in _call, so no frame above holds it as a local. Any exception
        may carry the request URL (and so the token): keep only the class name."""
        url = f"{_API}/bot{self.token}/{method}"
        try:
            return self.http.post(url, json=payload), "", False
        except Exception as exc:
            return None, type(exc).__name__, not isinstance(exc, _NOT_SENT)

    def _call(self, method: str, payload: dict[str, Any]) -> Any:
        for attempt in range(1, self.attempts + 1):
            wait = 2.0 ** (attempt - 1)
            response, network_error, ambiguous = self._post(method, payload)
            # Raise outside any except block so __context__ does not chain the httpx error.
            if response is None:
                if attempt == self.attempts or (ambiguous and method in _NOT_IDEMPOTENT):
                    raise TelegramError(f"{method}: {network_error}", ambiguous=ambiguous) from None
                self.sleep(wait)
                continue
            body = _json_object(response)
            if response.status_code == 200 and body.get("ok"):
                return body.get("result")
            if response.status_code == 200:
                # Telegram answered 200 but the body is not the expected one: it may have acted.
                raise TelegramError(f"{method}: HTTP 200", ambiguous=True) from None
            if response.status_code >= 500 and method in _NOT_IDEMPOTENT:
                # A 5xx can come from a proxy that lost the answer to a request Telegram already
                # acted on: repeating it could post the message twice.
                raise TelegramError(
                    f"{method}: HTTP {response.status_code}", ambiguous=True
                ) from None
            # 4xx (429 included): Telegram answered and did not act.
            retryable = response.status_code == 429 or response.status_code >= 500
            if not retryable or attempt == self.attempts:
                raise TelegramError(f"{method}: HTTP {response.status_code}") from None
            params = body.get("parameters")
            retry_after = params.get("retry_after") if isinstance(params, dict) else None
            self.sleep(_retry_delay(retry_after, wait))
        raise TelegramError(f"{method}: no attempts")  # attempts < 1

    def send_message(
        self, chat_id: str, text: str, reply_markup: dict[str, Any] | None = None
    ) -> int:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        result = self._call("sendMessage", payload)
        message_id = result.get("message_id") if isinstance(result, dict) else None
        if isinstance(message_id, bool) or not isinstance(message_id, int):
            # Accepted with HTTP 200, but no message id: the message may be in the chat.
            raise TelegramError("sendMessage: bad response", ambiguous=True) from None
        return message_id

    def get_updates(self, offset: int) -> list[dict[str, Any]]:
        result = self._call(
            "getUpdates", {"offset": offset, "timeout": 0, "allowed_updates": ["callback_query"]}
        )
        if not isinstance(result, list):
            raise TelegramError("getUpdates: bad response") from None
        return result

    def answer_callback(self, callback_id: str, text: str) -> None:
        self._call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text})

    def edit_reply_markup(
        self, chat_id: str, message_id: int, reply_markup: dict[str, Any] | None
    ) -> None:
        """Idempotent: setting the same keyboard twice is harmless. No markup removes it."""
        payload: dict[str, Any] = {"chat_id": chat_id, "message_id": message_id}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        self._call("editMessageReplyMarkup", payload)
