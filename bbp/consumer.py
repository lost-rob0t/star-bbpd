#!/usr/bin/env python3

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


@dataclass
class AckMessage:
    """Message to ack a RabbitMQ delivery."""

    delivery_tag: int


class TargetConsumer(pykka.ThreadingActor):
    """Consume strict 0.9 target documents and dispatch validated work."""

    def __init__(
        self,
        actor_name: str,
        rabbit_config: Dict[str, Any],
        publisher_ref: Any,
        tool_actor_ref: Any,
        actor_config: Dict[str, Any],
        routing_config: Dict[str, Any],
    ):
        super().__init__()
        self.actor_name = actor_name
        self.rabbit_config = rabbit_config
        self.publisher = publisher_ref
        self.tool_actor = tool_actor_ref
        self.actor_config = actor_config
        self.routing_config = routing_config
        self.queue_name = actor_config.get("queue", f"bbp.{actor_name}.targets")
        self.prefetch = actor_config.get("prefetch", 1)
        self.consume_thread = None
        self.connection = None
        self.channel = None
        self._stop_consuming = False
        self._pending_acks: Dict[int, bool] = {}
        self._conn_lock = threading.Lock()

    def _call_in_consume_thread(self, fn) -> bool:
        with self._conn_lock:
            conn = self.connection
        if not conn:
            return False
        try:
            conn.add_callback_threadsafe(fn)
            return True
        except Exception as exc:
            log.debug("[%s] add_callback_threadsafe failed: %s", self.actor_name, exc)
            return False

    def on_start(self):
        log.info("[%s] Starting TargetConsumer", self.actor_name)
        self._stop_consuming = False
        self.consume_thread = threading.Thread(
            target=self._consume_loop,
            daemon=True,
            name=f"consumer-{self.actor_name}",
        )
        self.consume_thread.start()

    def on_stop(self):
        log.info("[%s] Stopping TargetConsumer", self.actor_name)
        self._stop_consuming = True

        if self.channel and self.channel.is_open:
            try:
                self.channel.stop_consuming()
            except Exception as exc:
                if "pop from an empty deque" in str(exc) or "StreamLostError" in str(exc):
                    log.debug("[%s] Ignoring expected shutdown error: %s", self.actor_name, exc)
                else:
                    log.warning("[%s] Error stopping consumer: %s", self.actor_name, exc)

        if self.consume_thread and self.consume_thread.is_alive():
            self.consume_thread.join(timeout=5)

        if self.connection and self.connection.is_open:
            try:
                self.connection.close()
            except Exception as exc:
                if "pop from an empty deque" in str(exc) or "StreamLostError" in str(exc):
                    log.debug("[%s] Ignoring expected close error: %s", self.actor_name, exc)
                else:
                    log.warning("[%s] Error closing connection: %s", self.actor_name, exc)

    def on_receive(self, message):
        if isinstance(message, AckMessage):
            self._handle_ack(message.delivery_tag)
        else:
            log.warning("[%s] Unknown message type: %s", self.actor_name, type(message))
        return None

    def _consume_loop(self):
        try:
            params = pika.ConnectionParameters(
                host=self.rabbit_config.get("host", "localhost"),
                port=self.rabbit_config.get("port", 5672),
                virtual_host=self.rabbit_config.get("vhost", "/"),
                credentials=pika.PlainCredentials(
                    username=self.rabbit_config.get("username", "guest"),
                    password=self.rabbit_config.get("password", "guest"),
                ),
                heartbeat=self.rabbit_config.get("heartbeat_sec", 30),
            )

            log.debug("[%s] Creating dedicated RabbitMQ connection", self.actor_name)
            self.connection = pika.BlockingConnection(params)
            self.channel = self.connection.channel()
            self.channel.basic_qos(prefetch_count=self.prefetch)
            self.channel.basic_consume(
                queue=self.queue_name,
                on_message_callback=self._handle_target,
                auto_ack=False,
            )
            self.channel.start_consuming()
        except Exception as exc:
            log.error("[%s] Error in consume loop: %s", self.actor_name, exc, exc_info=True)
        finally:
            if self.connection and self.connection.is_open:
                try:
                    self.connection.close()
                except Exception as exc:
                    log.warning("[%s] Error closing connection: %s", self.actor_name, exc)
        log.info("[%s] Consume loop exited", self.actor_name)

    def _handle_target(self, channel, method, _properties, body):
        log.debug(
            "[%s] Received target delivery_tag=%s body_length=%s",
            self.actor_name,
            method.delivery_tag,
            len(body),
        )
        try:
            try:
                target = decode_target(body)
            except DocumentBoundaryError as exc:
                log.warning("[%s] Target rejected (%s): %s", self.actor_name, exc.category, exc)
                self._emit_parse_error(exc.category, str(exc), "")
                channel.basic_ack(delivery_tag=method.delivery_tag)
                return

            if not self._validate_target(target):
                channel.basic_ack(delivery_tag=method.delivery_tag)
                return

            log.info(
                "[%s] Received target: %s (id=%s)",
                self.actor_name,
                target.target,
                target.id,
            )
            self.tool_actor.tell(ScanTarget(target, method.delivery_tag, self.actor_ref))
        except Exception as exc:
            log.error("[%s] Error handling target: %s", self.actor_name, exc, exc_info=True)
            channel.basic_ack(delivery_tag=method.delivery_tag)

    def _validate_target(self, target: TargetDocument) -> bool:
        if target.actor != self.actor_name:
            self._emit_parse_error(
                "target_actor_mismatch",
                f"expected actor {self.actor_name!r}, got {target.actor!r}",
                target.id,
            )
            return False
        return True

    def _emit_parse_error(self, category: str, details: str, source_id: str):
        event = create_event(
            self.actor_name,
            "parse-error",
            f"category={category}; {details}",
            source_id,
        )
        routing_key = self.routing_config["event_key"].format(eventType="parse-error")
        self.publisher.tell(PublishEvent(event, routing_key))

    def _handle_ack(self, delivery_tag: int):
        def _do_ack():
            try:
                if self.channel and self.channel.is_open:
                    self.channel.basic_ack(delivery_tag=delivery_tag)
            except Exception as exc:
                log.error(
                    "[%s] Failed to ack delivery_tag=%s: %s",
                    self.actor_name,
                    delivery_tag,
                    exc,
                )

        if not self._call_in_consume_thread(_do_ack):
            log.warning("[%s] Ack dropped; connection not ready", self.actor_name)
