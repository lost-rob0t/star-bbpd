#!/usr/bin/env python3

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any, Dict

import pika
import pykka

from bbp.document_boundary import DocumentBoundaryError, TargetDocument, decode_target
from bbp.events import create_event
from bbp.publisher_pool import PublishEvent
from bbp.tool_actor import ScanTarget

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AckMessage:
    delivery_tag: int


@dataclass(frozen=True, slots=True)
class NackMessage:
    delivery_tag: int
    requeue: bool


@dataclass(frozen=True, slots=True)
class StopIntake:
    pass


class TargetConsumer(pykka.ThreadingActor):
    """Dispatch targets and apply settlement decisions on the pika owner thread."""

    def __init__(
        self,
        actor_name: str,
        rabbit_config: Dict[str, Any],
        publisher_ref: Any,
        tool_actor_ref: Any,
        actor_config: Dict[str, Any],
        routing_config: Dict[str, Any],
    ) -> None:
        super().__init__()
        self.actor_name = actor_name
        self.rabbit_config = rabbit_config
        self.publisher = publisher_ref
        self.tool_actor = tool_actor_ref
        self.routing_config = routing_config
        self.queue_name = actor_config.get("queue", f"bbp.{actor_name}.targets")
        self.prefetch = actor_config.get("prefetch", 1)
        self.shutdown_timeout = float(actor_config.get("shutdown_timeout_sec", 60))
        self.consume_thread: threading.Thread | None = None
        self.connection: pika.BlockingConnection | None = None
        self.channel = None
        self._stop_consuming = False
        self._close_connection = threading.Event()
        self._intake_stopped = threading.Event()
        self._conn_lock = threading.Lock()

    def on_start(self) -> None:
        self._stop_consuming = False
        self._close_connection.clear()
        self._intake_stopped.clear()
        self.consume_thread = threading.Thread(
            target=self._consume_loop,
            daemon=True,
            name=f"consumer-{self.actor_name}",
        )
        self.consume_thread.start()

    def on_receive(self, message: object):
        if isinstance(message, StopIntake):
            return self._stop_intake()
        if isinstance(message, AckMessage):
            self._handle_ack(message.delivery_tag)
        elif isinstance(message, NackMessage):
            self._handle_nack(message.delivery_tag, message.requeue)
        else:
            log.warning("[%s] unknown message type: %s", self.actor_name, type(message))
        return None

    def on_stop(self) -> None:
        self._stop_intake()
        self._close_connection.set()
        thread = self.consume_thread
        if thread and thread.is_alive():
            thread.join(timeout=self.shutdown_timeout)
        if thread and thread.is_alive():
            raise RuntimeError(f"consumer {self.actor_name} did not stop cleanly")

    def _call_in_consume_thread(self, callback) -> bool:
        with self._conn_lock:
            connection = self.connection
        if not connection or not connection.is_open:
            return False
        try:
            connection.add_callback_threadsafe(callback)
            return True
        except Exception as exc:
            log.debug("[%s] callback scheduling failed: %s", self.actor_name, exc)
            return False

    def _stop_intake(self) -> bool:
        """Cancel new deliveries but preserve the connection for late ACK/NACK."""
        if self._intake_stopped.is_set():
            return True
        self._stop_consuming = True

        def stop() -> None:
            if self.channel and self.channel.is_open:
                self.channel.stop_consuming()

        self._call_in_consume_thread(stop)
        if not self._intake_stopped.wait(timeout=self.shutdown_timeout):
            raise RuntimeError(f"consumer {self.actor_name} intake did not stop cleanly")
        return True

    def _consume_loop(self) -> None:
        connection = None
        try:
            params = pika.ConnectionParameters(
                host=self.rabbit_config.get("host", "localhost"),
                port=self.rabbit_config.get("port", 5672),
                virtual_host=self.rabbit_config.get("vhost", "/"),
                credentials=pika.PlainCredentials(
                    self.rabbit_config.get("username", "guest"),
                    self.rabbit_config.get("password", "guest"),
                ),
                heartbeat=self.rabbit_config.get("heartbeat_sec", 30),
            )
            connection = pika.BlockingConnection(params)
            channel = connection.channel()
            with self._conn_lock:
                self.connection = connection
                self.channel = channel

            if not self._stop_consuming:
                channel.basic_qos(prefetch_count=self.prefetch)
                channel.basic_consume(
                    queue=self.queue_name,
                    on_message_callback=self._handle_target,
                    auto_ack=False,
                )
                channel.start_consuming()

            self._intake_stopped.set()
            while connection.is_open and not self._close_connection.is_set():
                connection.process_data_events(time_limit=0.1)
        except Exception as exc:
            if not self._stop_consuming:
                log.error("[%s] consume loop failed: %s", self.actor_name, exc, exc_info=True)
        finally:
            self._intake_stopped.set()
            if connection and connection.is_open:
                try:
                    connection.close()
                except Exception as exc:
                    log.warning("[%s] connection close failed: %s", self.actor_name, exc)
            with self._conn_lock:
                self.connection = None
                self.channel = None

    def _handle_target(self, channel, method, _properties, body) -> None:
        try:
            try:
                target = decode_target(body)
            except DocumentBoundaryError as exc:
                self._emit_parse_error(exc.category, str(exc), "")
                channel.basic_ack(delivery_tag=method.delivery_tag)
                return

            if not self._validate_target(target):
                channel.basic_ack(delivery_tag=method.delivery_tag)
                return

            self.tool_actor.tell(
                ScanTarget(
                    target=target,
                    delivery_tag=method.delivery_tag,
                    consumer_ref=self.actor_ref,
                    redelivered=bool(getattr(method, "redelivered", False)),
                )
            )
        except Exception as exc:
            log.error("[%s] target dispatch failed: %s", self.actor_name, exc, exc_info=True)
            channel.basic_nack(
                delivery_tag=method.delivery_tag,
                requeue=not bool(getattr(method, "redelivered", False)),
            )

    def _validate_target(self, target: TargetDocument) -> bool:
        if target.actor == self.actor_name:
            return True
        self._emit_parse_error(
            "target_actor_mismatch",
            f"expected actor {self.actor_name!r}, got {target.actor!r}",
            target.id,
        )
        return False

    def _emit_parse_error(self, category: str, details: str, source_id: str) -> None:
        event = create_event(
            self.actor_name,
            "parse-error",
            f"category={category}; disposition=ack; {details}",
            source_id,
        )
        routing_key = self.routing_config["event_key"].format(eventType="parse-error")
        try:
            self.publisher.tell(PublishEvent(event, routing_key))
        except Exception as exc:
            log.warning("[%s] best-effort parse event failed: %s", self.actor_name, exc)

    def _handle_ack(self, delivery_tag: int) -> None:
        def ack() -> None:
            if self.channel and self.channel.is_open:
                self.channel.basic_ack(delivery_tag=delivery_tag)

        if not self._call_in_consume_thread(ack):
            log.warning("[%s] ACK could not be scheduled for %s", self.actor_name, delivery_tag)

    def _handle_nack(self, delivery_tag: int, requeue: bool) -> None:
        def nack() -> None:
            if self.channel and self.channel.is_open:
                self.channel.basic_nack(delivery_tag=delivery_tag, requeue=requeue)

        if not self._call_in_consume_thread(nack):
            log.warning(
                "[%s] NACK could not be scheduled for %s (requeue=%s)",
                self.actor_name,
                delivery_tag,
                requeue,
            )
