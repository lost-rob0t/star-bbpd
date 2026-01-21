#!/usr/bin/env python3

import json
import logging
import threading
from dataclasses import dataclass
from typing import Any, Dict, Optional

import pika
import pykka

from bbp.events import create_event
from bbp.publisher import PublishEvent
from bbp.tool_actor import ScanTarget

# Import starintel_doc for Target validation
from starintel_doc.targets import Target

log = logging.getLogger(__name__)


@dataclass
class AckMessage:
    """Message to ack a RabbitMQ delivery"""

    delivery_tag: int


class TargetConsumer(pykka.ThreadingActor):
    """
    Target consumer actor.

    Consumes Target documents from RabbitMQ queue, validates them,
    and forwards to ToolActor for processing.

    Runs pika consumer in separate daemon thread.
    """

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
        self._pending_acks: Dict[int, bool] = {}  # delivery_tag -> should_ack

    def on_start(self):
        """Start consume thread when actor starts"""
        log.info(f"[{self.actor_name}] Starting TargetConsumer")
        self._stop_consuming = False
        self.consume_thread = threading.Thread(
            target=self._consume_loop, daemon=True, name=f"consumer-{self.actor_name}"
        )
        self.consume_thread.start()

    def on_stop(self):
        """Stop consume thread when actor stops"""
        log.info(f"[{self.actor_name}] Stopping TargetConsumer")
        self._stop_consuming = True

        # Try to stop consuming gracefully, but ignore pika errors during shutdown
        if self.channel and self.channel.is_open:
            try:
                self.channel.stop_consuming()
            except Exception as e:
                # Ignore pika connection errors during shutdown - these are expected
                if "pop from an empty deque" in str(e) or "StreamLostError" in str(e):
                    log.debug(f"[{self.actor_name}] Ignoring expected pika shutdown error: {e}")
                else:
                    log.warning(f"[{self.actor_name}] Error stopping consumer: {e}")

        # Wait for thread to finish
        if self.consume_thread and self.consume_thread.is_alive():
            self.consume_thread.join(timeout=5)

        # Close connection if still open (ignore errors)
        if self.connection and self.connection.is_open:
            try:
                self.connection.close()
            except Exception as e:
                if "pop from an empty deque" in str(e) or "StreamLostError" in str(e):
                    log.debug(f"[{self.actor_name}] Ignoring expected pika connection close error: {e}")
                else:
                    log.warning(f"[{self.actor_name}] Error closing connection: {e}")

    def on_receive(self, message):
        """Handle incoming messages"""
        if isinstance(message, AckMessage):
            self._handle_ack(message.delivery_tag)
        else:
            log.warning(f"[{self.actor_name}] Unknown message type: {type(message)}")

        return None

    def _consume_loop(self):
        """
        Consume loop (runs in separate thread).

        Creates its own RabbitMQ connection and blocks on start_consuming().
        """
        try:
            # Create dedicated connection for this consumer (thread-safe)
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

            log.debug(f"[{self.actor_name}] Creating dedicated RabbitMQ connection")
            self.connection = pika.BlockingConnection(params)
            self.channel = self.connection.channel()

            # Set prefetch
            self.channel.basic_qos(prefetch_count=self.prefetch)

            # Start consuming
            log.info(
                f"[{self.actor_name}] Consuming from queue: {self.queue_name} (prefetch={self.prefetch})"
            )
            self.channel.basic_consume(
                queue=self.queue_name,
                on_message_callback=self._handle_target,
                auto_ack=False,  # Manual ack
            )

            # Block on consuming (until stop_consuming called)
            self.channel.start_consuming()

        except Exception as e:
            log.error(
                f"[{self.actor_name}] Error in consume loop: {e}", exc_info=True
            )
        finally:
            # Close connection on exit
            if self.connection and self.connection.is_open:
                try:
                    self.connection.close()
                    log.debug(f"[{self.actor_name}] Closed RabbitMQ connection")
                except Exception as e:
                    log.warning(f"[{self.actor_name}] Error closing connection: {e}")

        log.info(f"[{self.actor_name}] Consume loop exited")

    def _handle_target(self, channel, method, properties, body):
        """
        Handle incoming target message from RabbitMQ.

        Args:
            channel: pika channel
            method: pika method (contains delivery_tag)
            properties: pika properties
            body: message body (bytes)
        """
        try:
            # Decode body
            body_str = body.decode("utf-8")

            # Parse JSON to Target object
            try:
                target = Target.from_json(body_str)
            except Exception as e:
                log.error(
                    f"[{self.actor_name}] Failed to parse target JSON: {e}, body: {body_str[:200]}"
                )
                self._emit_parse_error(f"Failed to parse JSON: {str(e)}", "")
                channel.basic_ack(delivery_tag=method.delivery_tag)
                return

            # Validate target
            if not self._validate_target(target):
                # Validation already emitted parse-error and acked
                channel.basic_ack(delivery_tag=method.delivery_tag)
                return

            # Forward to tool actor
            log.info(
                f"[{self.actor_name}] Received target: {target.target} (id={target.id})"
            )

            # Send ScanTarget message to tool actor
            # Include reference to self for ack callback
            self.tool_actor.tell(
                ScanTarget(target, method.delivery_tag, self.actor_ref)
            )

            # DO NOT ack yet - tool actor will tell us to ack after processing

        except Exception as e:
            log.error(
                f"[{self.actor_name}] Error handling target: {e}", exc_info=True
            )
            # Ack to prevent poison loop
            channel.basic_ack(delivery_tag=method.delivery_tag)

    def _validate_target(self, target: Target) -> bool:
        """
        Validate target document.

        Args:
            target: Target object

        Returns:
            True if valid, False otherwise (emits parse-error)
        """
        # Check dtype
        if not hasattr(target, "dtype") or target.dtype != "target":
            self._emit_parse_error(
                f"Invalid dtype: expected 'target', got '{getattr(target, 'dtype', 'missing')}'",
                getattr(target, "id", ""),
            )
            return False

        # Check actor matches
        if not hasattr(target, "actor") or target.actor != self.actor_name:
            self._emit_parse_error(
                f"Actor mismatch: expected '{self.actor_name}', got '{getattr(target, 'actor', 'missing')}'",
                getattr(target, "id", ""),
            )
            return False

        # Check required fields
        if not hasattr(target, "target") or not target.target:
            self._emit_parse_error(
                "Missing required field: 'target'", getattr(target, "id", "")
            )
            return False

        return True

    def _emit_parse_error(self, details: str, source_id: str):
        """
        Emit parse-error event.

        Args:
            details: Error details
            source_id: Source document ID (if available)
        """
        event = create_event(self.actor_name, "parse-error", details, source_id)
        routing_key = self.routing_config["event_key"].format(eventType="parse-error")
        self.publisher.tell(PublishEvent(event, routing_key))
        log.warning(f"[{self.actor_name}] Parse error: {details}")

    def _handle_ack(self, delivery_tag: int):
        """
        Handle ack request from tool actor.

        Args:
            delivery_tag: RabbitMQ delivery tag to ack
        """
        try:
            if self.channel and self.channel.is_open:
                self.channel.basic_ack(delivery_tag=delivery_tag)
                log.debug(f"[{self.actor_name}] Acked delivery_tag: {delivery_tag}")
            else:
                log.warning(
                    f"[{self.actor_name}] Cannot ack {delivery_tag}: channel closed"
                )
        except Exception as e:
            log.error(f"[{self.actor_name}] Error acking {delivery_tag}: {e}")
