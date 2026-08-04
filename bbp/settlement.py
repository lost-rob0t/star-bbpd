from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class FailureKind(str, Enum):
    TRANSIENT = "transient"
    PERMANENT = "permanent"


@dataclass(frozen=True, slots=True)
class PublishResult:
    ok: bool
    routing_key: str
    attempts: int
    failure_kind: FailureKind | None = None
    error: str = ""

    @classmethod
    def success(cls, routing_key: str, attempts: int) -> "PublishResult":
        return cls(ok=True, routing_key=routing_key, attempts=attempts)

    @classmethod
    def failure(
        cls,
        routing_key: str,
        attempts: int,
        failure_kind: FailureKind,
        error: str,
    ) -> "PublishResult":
        return cls(
            ok=False,
            routing_key=routing_key,
            attempts=attempts,
            failure_kind=failure_kind,
            error=error,
        )

    @property
    def transient(self) -> bool:
        return not self.ok and self.failure_kind is FailureKind.TRANSIENT


class RequiredPublishError(RuntimeError):
    def __init__(self, result: PublishResult) -> None:
        super().__init__(
            f"required publish failed for {result.routing_key}: {result.error}"
        )
        self.result = result


@dataclass(frozen=True, slots=True)
class DeliveryDecision:
    requeue: bool
    reason: str


def failure_decision(kind: FailureKind, *, redelivered: bool) -> DeliveryDecision:
    if kind is FailureKind.TRANSIENT and not redelivered:
        return DeliveryDecision(requeue=True, reason="transient-first-attempt")
    if kind is FailureKind.TRANSIENT:
        return DeliveryDecision(requeue=False, reason="transient-retry-exhausted")
    return DeliveryDecision(requeue=False, reason="permanent-failure")
