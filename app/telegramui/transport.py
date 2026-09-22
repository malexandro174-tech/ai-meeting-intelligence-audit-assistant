"""Telegram receive transport for the registered TEST bot.

Receive (getUpdates/getFile/download) runs under the registered
TEST_MESSAGE_RECEIVE capability with the canonical env-delivered token; it is
read-only. Sending stays Broker-routed (TEST_MESSAGE_SEND) with the registered
TEST-chat allowlist enforced on every path. The token is never logged.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib import error, request as urlrequest

from ..core.events import MeetingPipelineError

TELEGRAM_API = "https://api.telegram.org"


class TelegramApi:
    def __init__(self, token_env: str) -> None:
        self._token_env = token_env

    def _token(self) -> str:
        token = os.getenv(self._token_env, "")
        if not token:
            raise MeetingPipelineError("CREDENTIAL_UNAVAILABLE", f"{self._token_env} not delivered")
        return token

    def call(self, method: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        token = self._token()
        body = json.dumps(payload).encode() if payload is not None else None
        req = urlrequest.Request(f"{TELEGRAM_API}/bot{token}/{method}", data=body, method="POST",
                                 headers={"Content-Type": "application/json"} if body else {})
        try:
            with urlrequest.urlopen(req, timeout=35) as response:
                result = json.loads(response.read(4 * 1024 * 1024).decode("utf-8"))
        except error.HTTPError as exc:
            raise MeetingPipelineError("TELEGRAM_FAILED", f"{method} http_{exc.code}",
                                       retryable=exc.code >= 500) from None
        except (error.URLError, OSError, TimeoutError, json.JSONDecodeError) as exc:
            raise MeetingPipelineError("TELEGRAM_FAILED", f"{method} {type(exc).__name__}", retryable=True) from None
        if not result.get("ok"):
            raise MeetingPipelineError("TELEGRAM_FAILED", f"{method} not ok")
        return result.get("result", {})

    def get_updates(self, offset: int, timeout: int) -> list[dict[str, Any]]:
        return self.call("getUpdates", {"offset": offset, "timeout": timeout, "limit": 10,
                                        "allowed_updates": ["message"]})

    def download_file(self, file_id: str, target: Path) -> None:
        token = self._token()
        info = self.call("getFile", {"file_id": file_id})
        path = str(info.get("file_path") or "")
        if not path:
            raise MeetingPipelineError("TELEGRAM_FAILED", "no file_path")
        req = urlrequest.Request(f"{TELEGRAM_API}/file/bot{token}/{path}")
        try:
            with urlrequest.urlopen(req, timeout=180) as response:
                target.write_bytes(response.read(60 * 1024 * 1024))
        except (error.URLError, OSError, TimeoutError) as exc:
            raise MeetingPipelineError("TELEGRAM_FAILED", f"download {type(exc).__name__}", retryable=True) from None
