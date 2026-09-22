"""container_env transport: scoped environment delivery for the Docker/VPS runtime.

The canonical Mag_OS deployment injects the same variables the Broker injects
into one-shot workers (ASSEMBLYAI_API_KEY, DEEPSEEK_API_KEY, bot token) only
into this container's environment. Values are read inside these adapters,
used for one outbound call and never logged, persisted or returned.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any
from urllib import error, request as urlrequest

from .gateway import BrokerGatewayError, ScopedHandle

ASSEMBLYAI_BASE = "https://api.assemblyai.com/v2"
DEEPSEEK_BASE = "https://api.deepseek.com"

# Capabilities the deployment grants this container (mirrors Broker registry metadata).
GRANTED_CAPABILITIES: dict[str, set[str]] = {
    "assemblyai": {"TRANSCRIPTION_CREATE", "TRANSCRIPTION_READ", "AUDIO_UPLOAD", "SPEAKER_DIARIZATION",
                   "TRANSCRIPTION_STATUS", "TRANSCRIPT_RETRIEVE", "SPEAKER_IDENTIFICATION", "LANGUAGE_DETECTION"},
    "deepseek": {"MODEL_INFERENCE"},
    "telegram_course_test_bot": {"BOT_IDENTITY_READ", "TEST_MESSAGE_RECEIVE", "TEST_MESSAGE_SEND"},
}


def _http_json(method: str, url: str, *, headers: dict[str, str], body: bytes | None = None,
               timeout: int = 60) -> tuple[int, dict | None]:
    req = urlrequest.Request(url, data=body, method=method, headers=headers)
    try:
        with urlrequest.urlopen(req, timeout=timeout) as response:
            status_code = response.status
            raw = response.read(16 * 1024 * 1024)
    except error.HTTPError as exc:
        detail = exc.read(2048)
        raise BrokerGatewayError(
            "AUTH_FAILED" if exc.code in (401, 403) else "RATE_LIMITED" if exc.code == 429 else
            "PROVIDER_UNAVAILABLE" if 500 <= exc.code < 600 else "PROVIDER_REJECTED",
            f"http_{exc.code}", retryable=exc.code >= 500 or exc.code == 429) from None
    except (error.URLError, OSError, TimeoutError) as exc:
        raise BrokerGatewayError("PROVIDER_UNAVAILABLE", type(exc).__name__, retryable=True) from None
    try:
        parsed = json.loads(raw.decode("utf-8")) if raw else None
    except (UnicodeDecodeError, json.JSONDecodeError):
        parsed = None
    return status_code, parsed if isinstance(parsed, dict) else None


class ContainerEnvGateway:
    def __init__(self, requester_id: str, settings: Any) -> None:
        self.requester_id, self.settings = requester_id, settings

    def scoped_handle(self, service_id: str, capability: str) -> ScopedHandle:
        granted = capability in GRANTED_CAPABILITIES.get(service_id, set())
        return ScopedHandle(service_id=service_id, capability=capability,
                            reference=f"container-env://{service_id}/{capability}",
                            granted=granted, reason="" if granted else "CAPABILITY_NOT_GRANTED_TO_CONTAINER")

    def _require(self, service_id: str, capability: str) -> None:
        handle = self.scoped_handle(service_id, capability)
        if not handle.granted:
            raise BrokerGatewayError("BROKER_POLICY_DENIED", handle.reason)

    def speech(self) -> "_EnvSpeech":
        return _EnvSpeech(self)

    def analysis(self) -> "_EnvAnalysis":
        return _EnvAnalysis(self)

    def telegram(self) -> "_EnvTelegram":
        return _EnvTelegram(self, self.settings)


class _EnvSpeech:
    def __init__(self, gateway: ContainerEnvGateway) -> None:
        self._gateway = gateway

    def _key(self) -> str:
        key = os.getenv("ASSEMBLYAI_API_KEY", "")
        if not key:
            raise BrokerGatewayError("CREDENTIAL_UNAVAILABLE", "ASSEMBLYAI_API_KEY not delivered to container")
        return key

    def transcribe(self, media_path: Path, *, speaker_labels: bool = True,
                   language_detection: bool = True, speakers_expected: int = 0) -> dict[str, Any]:
        self._gateway._require("assemblyai", "TRANSCRIPTION_CREATE")
        key = self._key()
        media = Path(media_path).read_bytes()
        # 1) upload
        status, body = _http_json("POST", f"{ASSEMBLYAI_BASE}/upload",
                                  headers={"Authorization": key, "Content-Type": "application/octet-stream"},
                                  body=media, timeout=600)
        upload_url = str((body or {}).get("upload_url") or "")
        if not upload_url:
            raise BrokerGatewayError("UPLOAD_FAILED", "no upload_url returned")
        # 2) create transcription
        request_body: dict[str, Any] = {"audio_url": upload_url, "speaker_labels": speaker_labels,
                                        "language_detection": language_detection, "punctuate": True,
                                        "format_text": True}
        if speakers_expected > 0:
            request_body["speakers_expected"] = speakers_expected
        status, body = _http_json("POST", f"{ASSEMBLYAI_BASE}/transcript",
                                  headers={"Authorization": key, "Content-Type": "application/json"},
                                  body=json.dumps(request_body).encode("utf-8"), timeout=60)
        transcript_id = str((body or {}).get("id") or "")
        if not transcript_id:
            raise BrokerGatewayError("TRANSCRIPTION_FAILED", "no transcript id returned")
        # 3) bounded polling
        deadline = time.monotonic() + 900
        while time.monotonic() < deadline:
            status, body = _http_json("GET", f"{ASSEMBLYAI_BASE}/transcript/{transcript_id}",
                                      headers={"Authorization": key}, timeout=30)
            provider_status = str((body or {}).get("status") or "")
            if provider_status == "error":
                raise BrokerGatewayError("TRANSCRIPTION_FAILED", str((body or {}).get("error"))[:160])
            if provider_status == "completed":
                utterances = (body or {}).get("utterances") or []
                return {
                    "transcript_id": transcript_id, "status": "completed",
                    "text": str((body or {}).get("text") or ""),
                    "audio_duration": (body or {}).get("audio_duration"),
                    "language_code": (body or {}).get("language_code"),
                    "speaker_count": len({str(u.get("speaker")) for u in utterances if isinstance(u, dict)}),
                    "utterances": [{"speaker": str(u.get("speaker")), "text": str(u.get("text") or ""),
                                    "start": u.get("start"), "end": u.get("end")}
                                   for u in utterances if isinstance(u, dict)],
                }
            time.sleep(5)
        raise BrokerGatewayError("TIMEOUT", "transcription polling exceeded 900s", retryable=True)


class _EnvAnalysis:
    def __init__(self, gateway: ContainerEnvGateway) -> None:
        self._gateway = gateway

    def complete(self, system_prompt: str, user_payload: dict[str, Any]) -> dict[str, Any]:
        self._gateway._require("deepseek", "MODEL_INFERENCE")
        api_key = os.getenv("DEEPSEEK_API_KEY", "")
        if not api_key:
            # Wait-and-retry state: the deployment will deliver the credential later.
            raise BrokerGatewayError("CREDENTIAL_UNAVAILABLE", "DEEPSEEK key not delivered to container",
                                     retryable=True)
        model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")  # resolved provider default, not hardcoded model id in code paths
        payload = {"model": model, "temperature": 0.1, "response_format": {"type": "json_object"},
                   "messages": [{"role": "system", "content": system_prompt},
                                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)}]}
        status, body = _http_json("POST", f"{DEEPSEEK_BASE}/chat/completions",
                                  headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                                  body=json.dumps(payload).encode("utf-8"), timeout=180)
        return body or {}


class _EnvTelegram:
    def __init__(self, gateway: ContainerEnvGateway, settings: Any) -> None:
        self._gateway, self._settings = gateway, settings

    def _token(self) -> str:
        token = os.getenv(self._settings.telegram_bot_token_env, "")
        if not token:
            raise BrokerGatewayError("CREDENTIAL_UNAVAILABLE", "TEST bot token not delivered to container")
        return token

    def api(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        token = self._token()
        status, body = _http_json("POST", f"https://api.telegram.org/bot{token}/{method}",
                                  headers={"Content-Type": "application/json"},
                                  body=json.dumps(payload).encode("utf-8"), timeout=35)
        if not (body or {}).get("ok"):
            raise BrokerGatewayError("TELEGRAM_FAILED", f"{method} not ok", retryable=True)
        return body

    def send_message(self, chat_id: str, text: str) -> dict[str, Any]:
        self._gateway._require("telegram_course_test_bot", "TEST_MESSAGE_SEND")
        if chat_id not in self._settings.telegram_allowed_chat_ids:
            raise BrokerGatewayError("BROKER_POLICY_DENIED", f"chat {chat_id} is not in the registered TEST allowlist")
        return self.api("sendMessage", {"chat_id": chat_id, "text": text})
