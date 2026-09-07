#!/usr/bin/env python3

from __future__ import annotations

import itertools
import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import pika
import pykka
from pika.exceptions import AMQPError, NackError, UnroutableError

from bbp.rabbit import RabbitManager
from bbp.settlement import FailureKind, PublishResult

log = logging.getLogger(__name__)

# TODO(starlang-migration):
# Transport stays RabbitMQ (STAR-RESEARCH-041 durable-dataflow rule): this
# pool is a settlement adapter, not domain logic, and is expected to survive
# the StarLang migration as the BBPD runtime transport binding.

JSON_CONTENT_TYPE = "application/json"
JSON_CONTENT_ENCODING = "utf-8"


def document_properties(dtype: str) -> pika.BasicProperties:
    if not isinstance(dtype, str) or not dtype:
        raise ValueError("document dtype must be a non-empty string")
    return pika.BasicProperties(
        content_type=JSON_CONTENT_TYPE,
        content_encoding=JSON_CONTENT_ENCODING,
        delivery_mode=2,
        type=dtype,
    )


def event_properties() -> pika.BasicProperties:
    return pika.BasicProperties(
        content_type=JSON_CONTENT_TYPE,
        content_encoding=JSON_CONTENT_ENCODING,
        delivery_mode=2,
    )


@dataclass(frozen=True, slots=True)
class PublishDocument:
    doc: Any
    routing_key: str
    run_id: str = ""


@dataclass(frozen=True, slots=True)
class PublishRelation:
    rel: Any
    routing_key: str
    run_id: str = ""


@dataclass(frozen=True, slots=True)
class PublishEvent:
    event: Dict[str, Any]
    routing_key: str
    run_id: str = ""


PublishMessage = PublishDocument | PublishRelation | PublishEvent


class PublishAttemptError(RuntimeError):
    def __init__(
        self,
        failure_kind: FailureKind,
        attempts: int,
        message: str,
    ) -> None:
        super().__init__(message)
        self.failure_kind = failure_kind
        self.attempts = attempts


class PublisherWorker(pykka.ThreadingActor):
    """Publisher actor that owns one confirm-enabled pika channel."""

    def __init__(
        self, rabbit_manager: RabbitManager, config: Dict[str, Any], worker_id: int
    ):
        super().__init__()
        self.rabbit_manager = rabbit_manager
        self.config = config
        self.worker_id = worker_id
        self.documents_exchange = config.get("documents", {}).get(
            "exchange", "documents"
        )
        self.events_exchange = config.get("events", {}).get("exchange", "events")
        service = config.get("service", {})
        self.max_retries = int(service.get("publisher_max_retries", 3))
        configured_delays = service.get("publisher_retry_delays_sec", [1, 2, 4])
        self.retry_delays = [float(value) for value in configured_delays]
        self._connection: Optional[pika.BlockingConnection] = None
        self._channel: Optional[pika.channel.Channel] = None
        self._conn_lock = threading.Lock()

    def on_stop(self) -> None:
        self._reset_connection()

    def _reset_connection(self) -> None:
        with self._conn_lock:
            try:
                if self._channel and getattr(self._channel, "is_open", False):
                    try:
                        self._channel.close()
                    except Exception:
                        pass
            finally:
                self._channel = None

            try:
                if self._connection and getattr(self._connection, "is_open", False):
                    try:
                        self._connection.close()
                    except Exception:
                        pass
            finally:
                self._connection = None

    def _ensure_channel(self) -> pika.channel.Channel:
        with self._conn_lock:
            if self._connection is None or not getattr(
                self._connection, "is_open", False
            ):
                self._connection = self.rabbit_manager.new_connection(
                    connection_name=f"bbp.publisher.{self.worker_id}"
                )
                self._channel = None

            if self._channel is None or not getattr(self._channel, "is_open", False):
                self._channel = self._connection.channel()
                self._channel.confirm_delivery()

            return self._channel

    def on_receive(self, message: object) -> PublishResult:
        if isinstance(message, PublishDocument):
            return self._publish_document(message)
        if isinstance(message, PublishRelation):
            return self._publish_relation(message)
        if isinstance(message, PublishEvent):
            return self._publish_event(message)
        return PublishResult.failure(
            routing_key="",
            attempts=0,
            failure_kind=FailureKind.PERMANENT,
            error=f"unknown publisher message: {type(message)!r}",
        )

    def _publish_document(self, message: PublishDocument) -> PublishResult:
        try:
            body = message.doc.to_json()
            properties = document_properties(message.doc.dtype)
        except Exception as exc:
            return self._serialization_failure(message.routing_key, exc)
        return self._settle(
            exchange=self.documents_exchange,
            routing_key=message.routing_key,
            body=body,
            properties=properties,
            source_id=getattr(message.doc, "id", ""),
            run_id=message.run_id,
            emit_error=True,
        )

    def _publish_relation(self, message: PublishRelation) -> PublishResult:
        try:
            body = message.rel.to_json()
            properties = document_properties(message.rel.dtype)
        except Exception as exc:
            return self._serialization_failure(message.routing_key, exc)
        return self._settle(
            exchange=self.documents_exchange,
            routing_key=message.routing_key,
            body=body,
            properties=properties,
            source_id=getattr(message.rel, "source", ""),
            run_id=message.run_id,
            emit_error=True,
        )

    def _publish_event(self, message: PublishEvent) -> PublishResult:
        try:
            body = json.dumps(message.event, separators=(",", ":"))
        except Exception as exc:
            return self._serialization_failure(message.routing_key, exc)
        return self._settle(
            exchange=self.events_exchange,
            routing_key=message.routing_key,
            body=body,
            properties=event_properties(),
            source_id=str(message.event.get("sourceId", "")),
            run_id=message.run_id,
            emit_error=message.event.get("eventType") != "publish-error",
        )

    def _serialization_failure(
        self, routing_key: str, exc: Exception
    ) -> PublishResult:
        return PublishResult.failure(
            routing_key=routing_key,
            attempts=0,
            failure_kind=FailureKind.PERMANENT,
            error=f"serialization failed: {exc}",
        )

    def _settle(
        self,
        *,
        exchange: str,
        routing_key: str,
        body: str,
        properties: pika.BasicProperties,
        source_id: str,
        run_id: str,
        emit_error: bool,
    ) -> PublishResult:
        try:
            attempts = self._publish_with_retry(
                exchange=exchange,
                routing_key=routing_key,
                body=body,
                properties=properties,
            )
            return PublishResult.success(routing_key, attempts)
        except PublishAttemptError as exc:
            result = PublishResult.failure(
                routing_key=routing_key,
                attempts=exc.attempts,
                failure_kind=exc.failure_kind,
                error=str(exc),
            )
            if emit_error:
                self._emit_publish_error(result, source_id, run_id)
            return result

    def _publish_with_retry(
        self,
        *,
        exchange: str,
        routing_key: str,
        body: str | bytes,
        properties: pika.BasicProperties,
    ) -> int:
        last_error: Exception | None = None
        encoded = body.encode(JSON_CONTENT_ENCODING) if isinstance(body, str) else body

        for attempt in range(1, self.max_retries + 1):
            try:
                confirmed = self._ensure_channel().basic_publish(
                    exchange=exchange,
                    routing_key=routing_key,
                    body=encoded,
                    properties=properties,
                    mandatory=True,
                )
                if confirmed is False:
                    last_error = RuntimeError(
                        "broker negatively acknowledged publish"
                    )
                else:
                    return attempt
            except UnroutableError as exc:
                raise PublishAttemptError(
                    FailureKind.PERMANENT,
                    attempt,
                    f"unroutable publish to {exchange}:{routing_key}",
                ) from exc
            except NackError as exc:
                last_error = exc
            except (AMQPError, OSError) as exc:
                last_error = exc

            if attempt < self.max_retries:
                delay = self.retry_delays[min(attempt - 1, len(self.retry_delays) - 1)]
                log.warning(
                    "[Publisher-%s] transient publish failure %s/%s for %s: %s",
                    self.worker_id,
                    attempt,
                    self.max_retries,
                    routing_key,
                    last_error,
                )
                self._reset_connection()
                time.sleep(delay)

        raise PublishAttemptError(
            FailureKind.TRANSIENT,
            self.max_retries,
            f"publish failed after {self.max_retries} attempts: {last_error}",
        )

    def _emit_publish_error(
        self, result: PublishResult, source_id: str, run_id: str
    ) -> None:
        from bbp.events import create_event

        event = create_event(
            actor_name=f"publisher-{self.worker_id}",
            event_type="publish-error",
            details=(
                f"run_id={run_id}; kind={result.failure_kind}; "
                f"routing_key={result.routing_key}; error={result.error}"
            ),
            source_id=source_id,
        )
        try:
            self._settle(
                exchange=self.events_exchange,
                routing_key="event.publish-error",
                body=json.dumps(event, separators=(",", ":")),
                properties=event_properties(),
                source_id=source_id,
                run_id=run_id,
                emit_error=False,
            )
        except Exception:
            pass


class PublisherPool:
    def __init__(
        self, rabbit_manager: RabbitManager, config: Dict[str, Any], pool_size: int = 3
    ):
        if pool_size <= 0:
            raise ValueError("publisher pool size must be positive")
        self.rabbit_manager = rabbit_manager
        self.config = config
        self.pool_size = pool_size
        self.workers: List[pykka.ActorRef] = []
        self.round_robin = itertools.cycle(range(pool_size))
        self._lock = threading.Lock()
        self.settlement_timeout = float(
            config.get("service", {}).get("publish_settlement_timeout_sec", 30)
        )
        self.shutdown_timeout = float(
            config.get("service", {}).get("shutdown_timeout_sec", 60)
        )

        for worker_id in range(pool_size):
            self.workers.append(
                PublisherWorker.start(rabbit_manager, config, worker_id)
            )

    def _get_next_worker(self) -> pykka.ActorRef:
        with self._lock:
            return self.workers[next(self.round_robin)]

    def tell(self, message: PublishMessage) -> None:
        self._get_next_worker().tell(message)

    def publish_required(
        self, message: PublishMessage, timeout: float | None = None
    ) -> PublishResult:
        routing_key = message.routing_key
        try:
            result = self._get_next_worker().ask(
                message,
                block=True,
                timeout=self.settlement_timeout if timeout is None else timeout,
            )
        except Exception as exc:
            return PublishResult.failure(
                routing_key=routing_key,
                attempts=0,
                failure_kind=FailureKind.TRANSIENT,
                error=f"publisher actor settlement failed: {exc}",
            )
        if not isinstance(result, PublishResult):
            return PublishResult.failure(
                routing_key=routing_key,
                attempts=0,
                failure_kind=FailureKind.PERMANENT,
                error=f"publisher returned invalid settlement {type(result)!r}",
            )
        return result

    def stop(self, timeout: float | None = None) -> None:
        wait = self.shutdown_timeout if timeout is None else timeout
        for worker in self.workers:
            worker.stop(block=True, timeout=wait)
        self.workers.clear()

    def get_proxy(self) -> "PublisherPoolProxy":
        return PublisherPoolProxy(self)


class PublisherPoolProxy:
    """Stable actor-like publisher facade."""

    def __init__(self, pool: PublisherPool):
        self.pool = pool

    def tell(self, message: PublishMessage) -> None:
        self.pool.tell(message)

    def publish_required(
        self, message: PublishMessage, timeout: float | None = None
    ) -> PublishResult:
        return self.pool.publish_required(message, timeout)

    def stop(self, timeout: float | None = None) -> None:
        self.pool.stop(timeout)
