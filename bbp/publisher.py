#!/usr/bin/env python3

"""
DEPRECATED: This module is deprecated and should not be used.
Use bbp.publisher_pool instead.

This single-threaded Publisher uses a shared RabbitMQ channel (get_publish_channel()),
which is NOT thread-safe and will cause random failures under concurrent load.

If you see this error, you're trying to import from the wrong module.
Update your imports to use: from bbp.publisher_pool import ...
"""

raise DeprecationWarning(
    "bbp.publisher is DEPRECATED. Use bbp.publisher_pool instead. "
    "This module uses unsafe shared RabbitMQ channels that cause thread-safety issues. "
    "Update all imports to: from bbp.publisher_pool import PublishDocument, PublishRelation, PublishEvent"
)

import json
import logging
import time
from dataclasses import dataclass
from typing import Dict, Any

import pika
import pykka

from bbp.rabbit import RabbitManager

log = logging.getLogger(__name__)


@dataclass
class PublishDocument:
    """Message to publish a document"""

    doc: Any  # StarIntel Document object
    routing_key: str


@dataclass
class PublishRelation:
    """Message to publish a relation"""

    rel: Any  # StarIntel Relation object
    routing_key: str


@dataclass
class PublishEvent:
    """Message to publish an event"""

    event: Dict[str, Any]
    routing_key: str


class Publisher(pykka.ThreadingActor):
    """
    Publisher actor for documents, relations, and events.

    Handles all RabbitMQ publishing with retry logic and error handling.
    """

    def __init__(self, rabbit_manager: RabbitManager, config: Dict[str, Any]):
        super().__init__()
        self.rabbit_manager = rabbit_manager
        self.config = config
        self.documents_exchange = config.get("documents", {}).get(
            "exchange", "documents"
        )
        self.events_exchange = config.get("events", {}).get("exchange", "events")
        self.max_retries = 3
        self.retry_delays = [1, 2, 4]  # exponential backoff in seconds

    def on_receive(self, message):
        """Handle incoming messages"""
        if isinstance(message, PublishDocument):
            self._publish_document(message.doc, message.routing_key)
        elif isinstance(message, PublishRelation):
            self._publish_relation(message.rel, message.routing_key)
        elif isinstance(message, PublishEvent):
            self._publish_event(message.event, message.routing_key)
        else:
            log.warning(f"Unknown message type: {type(message)}")

        return None

    def _publish_document(self, doc: Any, routing_key: str):
        """
        Publish a StarIntel document to the documents exchange.

        Args:
            doc: Document object with .to_json() method
            routing_key: Routing key for the document
        """
        try:
            # Serialize document to JSON
            body = doc.to_json()

            # Publish with retry logic
            self._publish_with_retry(
                exchange=self.documents_exchange,
                routing_key=routing_key,
                body=body,
                properties=pika.BasicProperties(
                    content_type="application/json",
                    delivery_mode=2,  # persistent
                    type=doc.dtype,
                ),
            )

            log.debug(
                f"Published document: {doc.dtype} ({doc.id if hasattr(doc, 'id') else 'unknown'}) -> {routing_key}"
            )

        except Exception as e:
            log.error(f"Failed to publish document: {e}", exc_info=True)
            # Emit publish-error event (best effort)
            from bbp.events import create_event

            error_event = create_event(
                actor_name="publisher",
                event_type="publish-error",
                details=f"Failed to publish {doc.dtype}: {str(e)}",
                source_id=doc.id if hasattr(doc, "id") else "",
            )
            try:
                self._publish_event_direct(error_event)
            except Exception:
                pass  # best effort

    def _publish_relation(self, rel: Any, routing_key: str):
        """
        Publish a StarIntel relation to the documents exchange.

        Args:
            rel: Relation object with .to_json() method
            routing_key: Routing key for the relation (typically "document.new.relation")
        """
        try:
            # Serialize relation to JSON
            body = rel.to_json()

            # Publish with retry logic
            self._publish_with_retry(
                exchange=self.documents_exchange,
                routing_key=routing_key,
                body=body,
                properties=pika.BasicProperties(
                    content_type="application/json",
                    delivery_mode=2,  # persistent
                    type="relation",
                ),
            )

            log.debug(
                f"Published relation: {rel.predicate} ({rel.source} -> {rel.target}) -> {routing_key}"
            )

        except Exception as e:
            log.error(f"Failed to publish relation: {e}", exc_info=True)
            # Emit publish-error event (best effort)
            from bbp.events import create_event

            error_event = create_event(
                actor_name="publisher",
                event_type="publish-error",
                details=f"Failed to publish relation: {str(e)}",
                source_id=rel.source if hasattr(rel, "source") else "",
            )
            try:
                self._publish_event_direct(error_event)
            except Exception:
                pass  # best effort

    def _publish_event(self, event: Dict[str, Any], routing_key: str):
        """
        Publish an event to the events exchange.

        Args:
            event: Event dictionary (from create_event())
            routing_key: Routing key for the event
        """
        self._publish_event_direct(event, routing_key)

    def _publish_event_direct(
        self, event: Dict[str, Any], routing_key: str = None
    ):
        """
        Publish event directly (for internal use, including error events).

        Args:
            event: Event dictionary
            routing_key: Routing key (defaults to "event.{eventType}")
        """
        try:
            # Generate routing key if not provided
            if routing_key is None:
                routing_key = f"event.{event['eventType']}"

            # Serialize event to JSON
            body = json.dumps(event)

            # Publish with retry logic
            self._publish_with_retry(
                exchange=self.events_exchange,
                routing_key=routing_key,
                body=body,
                properties=pika.BasicProperties(
                    content_type="application/json",
                    delivery_mode=2,  # persistent
                ),
            )

            log.debug(
                f"Published event: {event['eventType']} ({event['actorName']}) -> {routing_key}"
            )

        except Exception as e:
            log.error(f"Failed to publish event: {e}", exc_info=True)

    def _publish_with_retry(
        self,
        exchange: str,
        routing_key: str,
        body: str,
        properties: pika.BasicProperties,
    ):
        """
        Publish to RabbitMQ with retry logic.

        Args:
            exchange: Exchange name
            routing_key: Routing key
            body: Message body (JSON string)
            properties: pika BasicProperties

        Raises:
            Exception: If all retries fail
        """
        last_error = None

        for attempt in range(self.max_retries):
            try:
                channel = self.rabbit_manager.get_publish_channel()
                channel.basic_publish(
                    exchange=exchange,
                    routing_key=routing_key,
                    body=body.encode("utf-8") if isinstance(body, str) else body,
                    properties=properties,
                )
                return  # success

            except Exception as e:
                last_error = e
                if attempt < self.max_retries - 1:
                    delay = self.retry_delays[attempt]
                    log.warning(
                        f"Publish failed (attempt {attempt + 1}/{self.max_retries}): {e}. Retrying in {delay}s..."
                    )
                    time.sleep(delay)
                else:
                    log.error(
                        f"Publish failed after {self.max_retries} attempts: {e}"
                    )

        # All retries failed
        raise last_error
