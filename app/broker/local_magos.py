"""magos_local transport: direct Access & Integration Broker usage inside the Mag_OS runtime."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from .gateway import BrokerGatewayError, ScopedHandle

MAG_OS_ROOT = Path(__file__).resolve().parents[3]


class LocalMagOsGateway:
    """Thin capability-bounded facade over the real Mag_OS Broker (no credential exposure)."""

    def __init__(self, requester_id: str) -> None:
        self.requester_id = requester_id
        broker_root = MAG_OS_ROOT / "Core" / "AccessIntegrationBroker"
        if str(broker_root.parent.parent) not in sys.path:
            sys.path.insert(0, str(broker_root.parent.parent))
        from Core.AccessIntegrationBroker.broker.service import get_broker
        self._broker = get_broker()

    # ------------------------------------------------------------- policy

    def _request(self, service_id: str, capability: str, purpose: str) -> ScopedHandle:
        from Core.AccessIntegrationBroker.broker.models import AccessRequest
        decision = self._broker.request_access(AccessRequest(
            requester_id=self.requester_id, requester_type="MAG_OS_MODULE", service_id=service_id,
            requested_capability=capability, purpose=purpose, risk="LOW"))
        handle = decision.get("connection_handle") or {}
        return ScopedHandle(service_id=service_id, capability=capability,
                            reference=str(handle.get("client_reference") or ""),
                            granted=decision.get("result") == "GRANTED",
                            reason=str(decision.get("reason") or ""))

    def scoped_handle(self, service_id: str, capability: str) -> ScopedHandle:
        return self._request(service_id, capability, "portfolio integration audit")

    # ------------------------------------------------------------- speech

    def speech(self) -> "_LocalSpeech":
        return _LocalSpeech(self._broker, self.requester_id)

    # ------------------------------------------------------------ analysis

    def analysis(self) -> "_LocalAnalysis":
        return _LocalAnalysis(self._broker, self.requester_id)

    # ------------------------------------------------------------ telegram

    def telegram(self) -> "_LocalTelegram":
        return _LocalTelegram(self._broker, self.requester_id)


class _LocalSpeech:
    def __init__(self, broker: Any, requester_id: str) -> None:
        self._broker, self._requester_id = broker, requester_id

    def transcribe(self, media_path: Path, *, speaker_labels: bool = True,
                   language_detection: bool = True, speakers_expected: int = 0) -> dict[str, Any]:
        """upload -> create (diarization) -> poll -> normalized provider payload."""
        from .gateway import BrokerGatewayError  # local import avoids cycles
        try:
            operations = self._broker.assemblyai_operations()
            extra = {"speakers_expected": speakers_expected} if speakers_expected > 0 else None
            result = operations.transcribe_file(
                media_path, speaker_labels=speaker_labels, language_detection=language_detection,
                extra_options=extra, poll_interval_seconds=5, timeout_seconds=900,
                requester_id=self._requester_id, requester_type="MAG_OS_MODULE")
        except PermissionError as exc:
            raise BrokerGatewayError("BROKER_POLICY_DENIED", str(exc)[:200]) from None
        except RuntimeError as exc:  # AssemblyAiBrokerError and friends are RuntimeErrors
            code = str(exc).split(":", 1)[0]
            retryable = code in {"RATE_LIMITED", "PROVIDER_UNAVAILABLE", "TIMEOUT"}
            raise BrokerGatewayError(code or "TRANSCRIPTION_FAILED", retryable=retryable) from None
        return result


class _LocalAnalysis:
    def __init__(self, broker: Any, requester_id: str) -> None:
        self._broker, self._requester_id = broker, requester_id

    def complete(self, system_prompt: str, user_payload: dict[str, Any]) -> dict[str, Any]:
        """Canonical bounded inference: policy, injection and audit stay inside Broker."""
        import json as _json
        import os as _os

        from Core.AccessIntegrationBroker.broker.client import complete_deepseek
        from Core.AccessIntegrationBroker.broker.deepseek_adapter import DeepSeekBrokerError
        from Core.AccessIntegrationBroker.broker.models import AccessRequest
        # Worker contract: exact endpoint + deepseek-* model; resolved model comes from env, not code paths.
        payload = {
            "endpoint": "https://api.deepseek.com/chat/completions",
            "model": _os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": _json.dumps(user_payload, ensure_ascii=False)},
            ],
            "temperature": 0.1,
            "max_tokens": 800,
        }
        # Pre-flight capability check: an undelivered credential is a wait-and-retry state.
        decision = self._broker.request_access(AccessRequest(
            requester_id=self._requester_id, requester_type="MAG_OS_MODULE", service_id="deepseek",
            requested_capability="MODEL_INFERENCE", purpose="structured meeting audit", risk="READ_ONLY"))
        if decision.get("result") != "GRANTED":
            reason = str(decision.get("reason") or "")
            credential_gap = "CREDENTIAL" in reason.upper() or decision.get("result") == "VALIDATION_FAILED"
            raise BrokerGatewayError("CREDENTIAL_UNAVAILABLE" if credential_gap else "DEEPSEEK_BROKER_DENIED",
                                     reason[:160], retryable=credential_gap)
        try:
            return complete_deepseek(self._requester_id, "structured meeting audit", payload)["response"]
        except PermissionError as exc:
            raise BrokerGatewayError("DEEPSEEK_BROKER_DENIED", str(exc)[:160]) from None
        except DeepSeekBrokerError as exc:
            code = str(exc)
            retryable = any(marker in code for marker in ("CONNECTION", "HTTP_FAILED"))
            raise BrokerGatewayError("ANALYSIS_FAILED", code[:160], retryable=retryable) from None


class _LocalTelegram:
    def __init__(self, broker: Any, requester_id: str) -> None:
        self._broker, self._requester_id = broker, requester_id

    def send_message(self, chat_id: str, text: str) -> dict[str, Any]:
        return self._broker.send_course_test_message(self._requester_id, chat_id, text, requester_type="MAG_OS_MODULE")
