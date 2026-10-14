"""The only place that talks to the Telegram Bot API. The token is part of every URL, so no
error raised here carries a URL, an httpx message or Telegram's own description."""

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

_API = "https://api.telegram.org"


class TelegramError(Exception):
    pass


def _json_object(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


@dataclass
class TelegramClient:
    token: str = field(repr=False)
    http: httpx.Client
    sleep: Callable[[float], None] = field(default=time.sleep)
    attempts: int = 3

    def _post(self, url: str, payload: dict[str, Any]) -> tuple[httpx.Response | None, str]:
        # httpx exceptions carry the request URL (and so the token): keep only the class name.
        try:
            return self.http.post(url, json=payload), ""
        except (httpx.HTTPError, httpx.InvalidURL) as exc:
            return None, type(exc).__name__

    def _call(self, method: str, payload: dict[str, Any]) -> Any:
        url = f"{_API}/bot{self.token}/{method}"
        for attempt in range(1, self.attempts + 1):
            wait = 2.0 ** (attempt - 1)
            response, network_error = self._post(url, payload)
            # Raise outside any except block so __context__ does not chain the httpx error.
            if response is None:
                if attempt == self.attempts:
                    raise TelegramError(f"{method}: {network_error}") from None
                self.sleep(wait)
                continue
            body = _json_object(response)
            if response.status_code == 200 and body.get("ok"):
                return body.get("result")
            retryable = response.status_code == 429 or response.status_code >= 500
            if not retryable or attempt == self.attempts:
                raise TelegramError(f"{method}: HTTP {response.status_code}") from None
            params = body.get("parameters")
            retry_after = params.get("retry_after") if isinstance(params, dict) else None
            self.sleep(float(retry_after) if isinstance(retry_after, int | float) else wait)
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
        return int(result["message_id"])

    def get_updates(self, offset: int) -> list[dict[str, Any]]:
        result = self._call(
            "getUpdates", {"offset": offset, "timeout": 0, "allowed_updates": ["callback_query"]}
        )
        return list(result or [])

    def answer_callback(self, callback_id: str, text: str) -> None:
        self._call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text})
