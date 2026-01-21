#!/usr/bin/env python3

import json
import logging
import time
import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
import itertools

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


class PublisherWorker(pykka.ThreadingActor):
    """
    Single publisher worker actor.
    
    Handles publishing for a subset of documents/relations/events to distribute load.
    """

    def __init__(self, rabbit_manager: RabbitManager, config: Dict[str, Any], worker_id: int):
        super().__init__()
        self.rabbit_manager = rabbit_manager
        self.config = config
        self.worker_id = worker_id
        self.documents_exchange = config.get("documents", {}).get("exchange", "documents")
        self.events_exchange = config.get("events", {}).get("exchange", "events")
        self.max_retries = 3
        self.retry_delays = [1, 2, 4]  # exponential backoff in seconds
        # pika connections/channels are NOT thread-safe; each worker owns its own connection
        self._connection: Optional[pika.BlockingConnection] = None
        self._channel: Optional[pika.channel.Channel] = None
        self._conn_lock = threading.Lock()

    def on_stop(self) -> None:
        """Close our per-worker connection cleanly."""
        self._reset_connection()

    def _reset_connection(self) -> None:
        """Force a reconnect on next publish attempt."""
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
        """Ensure this worker has an open connection+channel (owned by this thread)."""
        with self._conn_lock:
            if self._connection is None or not getattr(self._connection, "is_open", False):
                self._connection = self.rabbit_manager.new_connection(
                    connection_name=f"bbp.publisher.{self.worker_id}"
                )
                self._channel = None

            if self._channel is None or not getattr(self._channel, "is_open", False):
                self._channel = self._connection.channel()

            return self._channel

    def on_receive(self, message):
        """Handle incoming messages"""
        if isinstance(message, PublishDocument):
            self._publish_document(message.doc, message.routing_key)
        elif isinstance(message, PublishRelation):
            self._publish_relation(message.rel, message.routing_key)
        elif isinstance(message, PublishEvent):
            self._publish_event(message.event, message.routing_key)
        else:
            log.warning(f"[Publisher-{self.worker_id}] Unknown message type: {type(message)}")

        return None

    def _publish_document(self, doc: Any, routing_key: str):
        """Publish a StarIntel document to documents exchange."""
        try:
            body = doc.to_json()
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
                f"[Publisher-{self.worker_id}] Published document: {doc.dtype} ({doc.id if hasattr(doc, 'id') else 'unknown'}) -> {routing_key}"
            )

        except Exception as e:
            log.error(f"[Publisher-{self.worker_id}] Failed to publish document: {e}", exc_info=True)
            self._emit_publish_error(f"Failed to publish {doc.dtype}: {str(e)}", doc.id if hasattr(doc, "id") else "")

    def _publish_relation(self, rel: Any, routing_key: str):
        """Publish a StarIntel relation to documents exchange."""
        try:
            body = rel.to_json()
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
                f"[Publisher-{self.worker_id}] Published relation: {rel.predicate} ({rel.source} -> {rel.target}) -> {routing_key}"
            )

        except Exception as e:
            log.error(f"[Publisher-{self.worker_id}] Failed to publish relation: {e}", exc_info=True)
            self._emit_publish_error(f"Failed to publish relation: {str(e)}", rel.source if hasattr(rel, "source") else "")

    def _publish_event(self, event: Dict[str, Any], routing_key: str):
        """Publish an event to events exchange."""
        self._publish_event_direct(event, routing_key)

    def _publish_event_direct(self, event: Dict[str, Any], routing_key: str = None):
        """Publish event directly (for internal use, including error events)."""
        try:
            if routing_key is None:
                routing_key = f"event.{event['eventType']}"
            
            body = json.dumps(event)
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
                f"[Publisher-{self.worker_id}] Published event: {event['eventType']} ({event['actorName']}) -> {routing_key}"
            )

        except Exception as e:
            log.error(f"[Publisher-{self.worker_id}] Failed to publish event: {e}", exc_info=True)

    def _publish_with_retry(self, exchange: str, routing_key: str, body: str, properties: pika.BasicProperties):
        """Publish to RabbitMQ with retry logic."""
        last_error = None

        for attempt in range(self.max_retries):
            try:
                channel = self._ensure_channel()
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
                        f"[Publisher-{self.worker_id}] Publish failed (attempt {attempt + 1}/{self.max_retries}): {e}. Retrying in {delay}s..."
                    )
                    self._reset_connection()
                    time.sleep(delay)
                else:
                    log.error(
                        f"[Publisher-{self.worker_id}] Publish failed after {self.max_retries} attempts: {e}"
                    )

        raise last_error

    def _emit_publish_error(self, details: str, source_id: str):
        """Emit publish-error event (best effort)."""
        from bbp.events import create_event
        
        error_event = create_event(
            actor_name=f"publisher-{self.worker_id}",
            event_type="publish-error",
            details=details,
            source_id=source_id,
        )
        try:
            self._publish_event_direct(error_event)
        except Exception:
            pass  # best effort


class PublisherPool:
    """
    Publisher pool that distributes publishing load across multiple worker actors.
    
    Uses round-robin distribution for load balancing.
    """

    def __init__(self, rabbit_manager: RabbitManager, config: Dict[str, Any], pool_size: int = 3):
        self.rabbit_manager = rabbit_manager
        self.config = config
        self.pool_size = pool_size
        self.workers: List[pykka.ActorRef] = []
        self.round_robin = itertools.cycle(range(pool_size))
        self._lock = threading.Lock()

        # Start worker actors
        for i in range(pool_size):
            worker = PublisherWorker.start(rabbit_manager, config, i)
            self.workers.append(worker)
            log.info(f"Started publisher worker {i}")

    def _get_next_worker(self) -> pykka.ActorRef:
        """Get next worker using round-robin."""
        with self._lock:
            worker_index = next(self.round_robin)
            return self.workers[worker_index]

    def publish_document(self, doc: Any, routing_key: str):
        """Publish document using next available worker."""
        worker = self._get_next_worker()
        worker.tell(PublishDocument(doc, routing_key))

    def publish_relation(self, rel: Any, routing_key: str):
        """Publish relation using next available worker."""
        worker = self._get_next_worker()
        worker.tell(PublishRelation(rel, routing_key))

    def publish_event(self, event: Dict[str, Any], routing_key: str):
        """Publish event using next available worker."""
        worker = self._get_next_worker()
        worker.tell(PublishEvent(event, routing_key))

    def stop(self):
        """Stop all worker actors."""
        log.info("Stopping publisher pool...")
        for i, worker in enumerate(self.workers):
            try:
                worker.stop()
                log.info(f"Stopped publisher worker {i}")
            except Exception as e:
                log.warning(f"Error stopping publisher worker {i}: {e}")
        self.workers.clear()

    def get_proxy(self):
        """Get a proxy object that mimics the original Publisher interface."""
        return PublisherPoolProxy(self)


class PublisherPoolProxy:
    """
    Proxy object that mimics the original Publisher interface but uses the pool.
    """

    def __init__(self, pool: PublisherPool):
        self.pool = pool

    def tell(self, message):
        """Handle tell() calls like a regular actor."""
        log.debug(f"PublisherPoolProxy received message type: {type(message)}")
        if isinstance(message, PublishDocument):
            log.debug(f"Publishing document to routing key: {message.routing_key}")
            self.pool.publish_document(message.doc, message.routing_key)
        elif isinstance(message, PublishRelation):
            log.debug(f"Publishing relation to routing key: {message.routing_key}")
            self.pool.publish_relation(message.rel, message.routing_key)
        elif isinstance(message, PublishEvent):
            log.debug(f"Publishing event to routing key: {message.routing_key}")
            self.pool.publish_event(message.event, message.routing_key)
        else:
            log.warning(f"PublisherPoolProxy: Unknown message type: {type(message)}")

    def stop(self):
        """Stop the underlying pool."""
        self.pool.stop()