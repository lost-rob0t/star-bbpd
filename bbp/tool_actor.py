#!/usr/bin/env python3

from __future__ import annotations

import logging
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import pykka
import ulid

from bbp.document_boundary import CanonicalDocument, TargetDocument
from bbp.events import create_event
from bbp.publisher_pool import PublishDocument, PublishEvent, PublishRelation
from bbp.settlement import (
    DeliveryDecision,
    FailureKind,
    PublishResult,
    RequiredPublishError,
    failure_decision,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ScanTarget:
    target: TargetDocument
    delivery_tag: int
    consumer_ref: Any
    redelivered: bool = False


@dataclass(frozen=True, slots=True)
class ScanExecutionResult:
    ok: bool
    run_id: str
    document_count: int
    relation_count: int
    failure_kind: FailureKind | None = None
    decision: DeliveryDecision | None = None
    error: str = ""


class ScanFailure(RuntimeError):
    def __init__(self, kind: FailureKind, message: str) -> None:
        super().__init__(message)
        self.kind = kind


class ToolActor(pykka.ThreadingActor, ABC):
    """Base actor that settles required outputs before delivery ACK."""

    def __init__(self, actor_name: str, publisher_ref: Any, tool_config: Dict[str, Any]):
        super().__init__()
        if not callable(getattr(publisher_ref, "publish_required", None)):
            raise TypeError("publisher must provide publish_required(message, timeout)")
        if not callable(getattr(publisher_ref, "tell", None)):
            raise TypeError("publisher must provide tell(message)")
        self.actor_name = actor_name
        self.publisher = publisher_ref
        self.tool_config = tool_config
        self.routing: Optional[Dict[str, Any]] = None
        self.publish_timeout = float(tool_config.get("publish_timeout_sec", 30))
        self._active_run_id = ""

    def on_receive(self, message):
        if isinstance(message, ScanTarget):
            return self._execute_scan(message)
        log.warning("Unknown message type: %s", type(message))
        return None

    def _execute_scan(self, scan: ScanTarget) -> ScanExecutionResult:
        target = scan.target
        run_id = str(ulid.new())
        self._active_run_id = run_id
        doc_count = 0
        rel_count = 0
        process: subprocess.Popen[str] | None = None

        self._emit_event(
            "scan-start",
            f"run_id={run_id}; starting {self.actor_name} scan for {target.target}",
            target.id,
            run_id,
        )

        try:
            cmd = self._build_command(target)
            log.info("[%s] run_id=%s executing: %s", self.actor_name, run_id, " ".join(cmd))
            timeout = self.tool_config.get("timeout_sec", 300)
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            context: Dict[str, Any] = {"run_id": run_id}

            if process.stdout is None:
                raise ScanFailure(FailureKind.TRANSIENT, "tool stdout pipe was not created")

            for line in iter(process.stdout.readline, ""):
                if not line:
                    break
                result = self._parse_line(line, target, context)
                if not result:
                    continue
                doc, relation = result
                if doc:
                    self._emit_document(doc, run_id)
                    doc_count += 1
                if relation:
                    self._emit_relation(relation, run_id)
                    rel_count += 1

            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired as exc:
                self._terminate_process(process)
                stderr = process.stderr.read()[-500:] if process.stderr else ""
                raise ScanFailure(
                    FailureKind.TRANSIENT,
                    f"tool timeout after {timeout}s; stderr={stderr}",
                ) from exc

            stderr_output = process.stderr.read() if process.stderr else ""
            if process.returncode != 0:
                raise ScanFailure(
                    FailureKind.TRANSIENT,
                    f"tool exit code {process.returncode}; stderr={stderr_output[-500:]}",
                )

            self._emit_event(
                "scan-finished",
                (
                    f"run_id={run_id}; finished {self.actor_name} scan; "
                    f"documents={doc_count}; relations={rel_count}"
                ),
                target.id,
                run_id,
            )
            self._send_ack(scan.consumer_ref, scan.delivery_tag)
            return ScanExecutionResult(
                ok=True,
                run_id=run_id,
                document_count=doc_count,
                relation_count=rel_count,
            )
        except RequiredPublishError as exc:
            kind = exc.result.failure_kind or FailureKind.TRANSIENT
            return self._settle_failure(
                scan,
                run_id,
                doc_count,
                rel_count,
                kind,
                str(exc),
            )
        except ScanFailure as exc:
            return self._settle_failure(
                scan,
                run_id,
                doc_count,
                rel_count,
                exc.kind,
                str(exc),
            )
        except Exception as exc:
            log.error(
                "[%s] run_id=%s scan failed: %s",
                self.actor_name,
                run_id,
                exc,
                exc_info=True,
            )
            return self._settle_failure(
                scan,
                run_id,
                doc_count,
                rel_count,
                FailureKind.TRANSIENT,
                f"unexpected scan failure: {exc}",
            )
        finally:
            if process is not None and process.poll() is None:
                self._terminate_process(process)
            self._active_run_id = ""

    def _settle_failure(
        self,
        scan: ScanTarget,
        run_id: str,
        document_count: int,
        relation_count: int,
        kind: FailureKind,
        error: str,
    ) -> ScanExecutionResult:
        decision = failure_decision(kind, redelivered=scan.redelivered)
        self._emit_event(
            "scan-error",
            (
                f"run_id={run_id}; kind={kind.value}; decision={decision.reason}; "
                f"error={error}"
            ),
            scan.target.id,
            run_id,
        )
        self._send_nack(scan.consumer_ref, scan.delivery_tag, decision.requeue)
        return ScanExecutionResult(
            ok=False,
            run_id=run_id,
            document_count=document_count,
            relation_count=relation_count,
            failure_kind=kind,
            decision=decision,
            error=error,
        )

    def _terminate_process(self, process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    def _require_publish(
        self, message: PublishDocument | PublishRelation
    ) -> PublishResult:
        result = self.publisher.publish_required(message, self.publish_timeout)
        if not isinstance(result, PublishResult):
            result = PublishResult.failure(
                message.routing_key,
                0,
                FailureKind.PERMANENT,
                f"invalid settlement result: {type(result)!r}",
            )
        if not result.ok:
            raise RequiredPublishError(result)
        return result


    def _emit_document(
        self, doc: CanonicalDocument, run_id: str = ""
    ) -> PublishResult:
        effective_run_id = run_id or getattr(self, "_active_run_id", "")
        return self._require_publish(
            PublishDocument(doc, self._document_key(doc), effective_run_id)
        )

    def _emit_relation(
        self, relation: CanonicalDocument, run_id: str = ""
    ) -> PublishResult:
        effective_run_id = run_id or getattr(self, "_active_run_id", "")
        return self._require_publish(
            PublishRelation(relation, self._relation_key(), effective_run_id)
        )

    def _send_ack(self, consumer_ref: Any, delivery_tag: int) -> None:
        from bbp.consumer import AckMessage

        consumer_ref.tell(AckMessage(delivery_tag))

    def _send_nack(
        self, consumer_ref: Any, delivery_tag: int, requeue: bool
    ) -> None:
        from bbp.consumer import NackMessage

        consumer_ref.tell(NackMessage(delivery_tag, requeue))

    @abstractmethod
    def _build_command(self, target: TargetDocument) -> List[str]:
        raise NotImplementedError

    @abstractmethod
    def _parse_line(
        self, line: str, target: TargetDocument, context: Dict[str, Any]
    ) -> Optional[Tuple[CanonicalDocument | None, CanonicalDocument | None]]:
        raise NotImplementedError

    def _require_routing(self) -> Dict[str, Any]:
        if not self.routing:
            raise RuntimeError(f"[{self.actor_name}] routing config is unavailable")
        return self.routing

    def _document_key(self, doc: CanonicalDocument) -> str:
        return self._require_routing()["document_ingest_key"].format(dtype=doc.dtype)

    def _relation_key(self) -> str:
        return self._require_routing()["document_ingest_key"].format(dtype="relation")

    def _emit_event(
        self,
        event_type: str,
        details: str,
        source_id: str,
        run_id: str,
    ) -> None:
        routing = self._require_routing()
        event = create_event(self.actor_name, event_type, details, source_id)
        routing_key = routing["event_key"].format(eventType=event_type)
        # Lifecycle events are explicitly best-effort; required documents and
        # relations use publish_required() above.
        try:
            self.publisher.tell(PublishEvent(event, routing_key, run_id))
        except Exception as exc:
            log.warning(
                "[%s] run_id=%s best-effort event %s failed: %s",
                self.actor_name,
                run_id,
                event_type,
                exc,
            )
