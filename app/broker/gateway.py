"""Broker gateway: the ONLY place external services are reached.

Two transports, one contract:

- ``magos_local`` — the product runs inside the Mag_OS runtime and calls the
  Access & Integration Broker Python API directly. Scoped handles and
  capability checks come from the Broker; no credential value ever reaches
  product code.
- ``container_env`` — the product runs in Docker on the VPS. The canonical
  Mag_OS deployment injects scoped environment variables into the container
  (same ephemeral env-delivery model the Broker uses locally). Product code
  still routes every call through this gateway, applies capability checks and
  never logs or persists the injected values.

Raw API keys/tokens are read only inside transport adapters, are never
returned by gateway methods and never appear in events or storage.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class ScopedHandle:
    """What callers receive instead of credentials."""
    service_id: str
    capability: str
    reference: str
    granted: bool
    reason: str = ""


class BrokerGatewayError(RuntimeError):
    def __init__(self, code: str, detail: str = "", retryable: bool = False) -> None:
        super().__init__(f"{code}: {detail}"[:300])
        self.code, self.detail, self.retryable = code, detail, retryable


class SpeechOperations(Protocol):
    def transcribe(self, media_path: Path, *, speaker_labels: bool = True,
                   language_detection: bool = True, speakers_expected: int = 0) -> dict[str, Any]: ...


class AnalysisOperations(Protocol):
    def complete(self, system_prompt: str, user_payload: dict[str, Any]) -> dict[str, Any]: ...


class TelegramOperations(Protocol):
    def send_message(self, chat_id: str, text: str) -> dict[str, Any]: ...


class BrokerGateway(Protocol):
    def scoped_handle(self, service_id: str, capability: str) -> ScopedHandle: ...
    def speech(self) -> SpeechOperations: ...
    def analysis(self) -> AnalysisOperations: ...
    def telegram(self) -> TelegramOperations: ...


def build_gateway(transport: str, requester_id: str, settings: Any) -> BrokerGateway:
    if transport == "magos_local":
        from .local_magos import LocalMagOsGateway
        return LocalMagOsGateway(requester_id)
    if transport == "container_env":
        from .container_env import ContainerEnvGateway
        return ContainerEnvGateway(requester_id, settings)
    raise BrokerGatewayError("BROKER_TRANSPORT_UNKNOWN", transport)
