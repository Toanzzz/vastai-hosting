import json
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

Transport = Callable[[str, dict[str, Any]], Any]


class TelegramError(Exception):
    def __init__(self, message: str, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


class TelegramApi:
    def __init__(self, token: str, transport: Transport | None = None) -> None:
        self._token = token
        self._transport = transport

    def call(self, method: str, **params: Any) -> Any:
        if self._transport is not None:
            return self._transport(method, params)
        payload = {key: value for key, value in params.items() if value is not None}
        request = urllib.request.Request(
            f"https://api.telegram.org/bot{self._token}/{method}",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        wait = payload.get("timeout")
        if method == "getUpdates" and isinstance(wait, int) and not isinstance(wait, bool):
            timeout = wait + 10
        else:
            timeout = 30
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = json.loads(response.read().decode())
        except urllib.error.HTTPError as error:
            # The exception text includes the request URL, which contains the bot token.
            raise self._from_body(error.read().decode(errors="replace")) from None
        except Exception as error:
            raise TelegramError(self._redact(str(error))) from None
        if not isinstance(body, dict) or body.get("ok") is not True:
            raise self._from_body(json.dumps(body))
        return body.get("result")

    def _from_body(self, detail: str) -> TelegramError:
        description = self._redact(detail)
        code: int | None = None
        try:
            parsed = json.loads(detail)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            error_code = parsed.get("error_code")
            if isinstance(error_code, int) and not isinstance(error_code, bool):
                code = error_code
            if isinstance(parsed.get("description"), str):
                description = self._redact(parsed["description"])
        return TelegramError(description, code)

    def _redact(self, text: str) -> str:
        return text.replace(self._token, "[redacted]")[:300]
