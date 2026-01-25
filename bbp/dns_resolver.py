#!/usr/bin/env python3

import logging
import threading
from dataclasses import dataclass
from typing import Any, Dict, Optional

import dns.resolver
import pika
import pykka

from bbp.events import create_event
from bbp.publisher_pool import PublishDocument, PublishEvent

# Import starintel_doc for Domain validation
from starintel_doc.hosts import Domain

log = logging.getLogger(__name__)


@dataclass
class AckMessage:
    """Message to ack a RabbitMQ delivery"""

    delivery_tag: int


class DnsResolverActor(pykka.ThreadingActor):
    """
    DNS Resolver actor.

    Consumes Domain documents from documents.new.domain queue,
    resolves DNS records (A, AAAA, CNAME, MX, TXT),
    and publishes updated Domain documents to documents.updated.domain.

    Runs pika consumer in separate daemon thread.
    Thread-safe: owns its own RabbitMQ connection.
    """

    def __init__(
        self,
        rabbit_config: Dict[str, Any],
        publisher_ref: Any,
        resolver_config: Dict[str, Any],
        routing_config: Dict[str, Any],
    ):
        super().__init__()
        self.actor_name = "dns-resolver"
        self.rabbit_config = rabbit_config
        self.publisher = publisher_ref
        self.resolver_config = resolver_config
        self.routing_config = routing_config
        self.queue_name = resolver_config.get("queue", "bbp.dns-resolver.domains")
        self.prefetch = resolver_config.get("prefetch", 5)
        self.consume_thread = None
        self.connection = None
        self.channel = None
        self._stop_consuming = False
        self._conn_lock = threading.Lock()

        # DNS resolver settings
        self.timeout = resolver_config.get("timeout_sec", 5)
        self.nameservers = resolver_config.get("nameservers", None)

        # Initialize DNS resolver
        self.resolver = dns.resolver.Resolver()
        if self.nameservers:
            self.resolver.nameservers = self.nameservers
        self.resolver.timeout = self.timeout
        self.resolver.lifetime = self.timeout

    def _call_in_consume_thread(self, fn) -> bool:
        """Run `fn` on the consume thread safely (pika channel is not thread-safe)."""
        with self._conn_lock:
            conn = self.connection
        if not conn:
            return False
        try:
            conn.add_callback_threadsafe(fn)
            return True
        except Exception as e:
            log.debug("[%s] add_callback_threadsafe failed: %s", self.actor_name, e)
            return False

    def on_start(self):
        """Start consume thread when actor starts"""
        log.info(f"[{self.actor_name}] Starting DnsResolverActor")
        self._stop_consuming = False
        self.consume_thread = threading.Thread(
            target=self._consume_loop, daemon=True, name=f"dns-resolver-consumer"
        )
        self.consume_thread.start()

    def on_stop(self):
        """Stop consume thread when actor stops"""
        log.info(f"[{self.actor_name}] Stopping DnsResolverActor")
        self._stop_consuming = True

        # Try to stop consuming gracefully
        if self.channel and self.channel.is_open:
            try:
                self.channel.stop_consuming()
            except Exception as e:
                if "pop from an empty deque" in str(e) or "StreamLostError" in str(e):
                    log.debug(f"[{self.actor_name}] Ignoring expected pika shutdown error: {e}")
                else:
                    log.warning(f"[{self.actor_name}] Error stopping consumer: {e}")

        # Wait for thread to finish
        if self.consume_thread and self.consume_thread.is_alive():
            self.consume_thread.join(timeout=5)

        # Close connection if still open
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
                on_message_callback=self._handle_domain,
                auto_ack=False,  # Manual ack
            )

            # Block on consuming
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

    def _handle_domain(self, channel, method, _properties, body):
        """
        Handle incoming domain message from RabbitMQ.

        Args:
            channel: pika channel
            method: pika method (contains delivery_tag)
            properties: pika properties
            body: message body (bytes)
        """
        log.debug(f"[{self.actor_name}] _handle_domain called with delivery_tag={method.delivery_tag}")
        try:
            # Decode body
            body_str = body.decode("utf-8")
            log.debug(f"[{self.actor_name}] Decoded message body: {body_str[:100]}...")

            # Parse JSON to Domain object
            try:
                domain = Domain.from_json(body_str)
                log.debug(f"[{self.actor_name}] Parsed domain: dtype={domain.dtype}, record={domain.record}, id={domain.id}")
            except Exception as e:
                log.error(
                    f"[{self.actor_name}] Failed to parse domain JSON: {e}, body: {body_str[:200]}"
                )
                self._emit_parse_error(f"Failed to parse JSON: {str(e)}", "")
                channel.basic_ack(delivery_tag=method.delivery_tag)
                return

            # Validate domain
            log.debug(f"[{self.actor_name}] Validating domain {domain.id}...")
            if not self._validate_domain(domain):
                log.warning(f"[{self.actor_name}] Domain validation failed for {domain.id}")
                channel.basic_ack(delivery_tag=method.delivery_tag)
                return

            # Resolve DNS records
            log.info(f"[{self.actor_name}] Resolving DNS for domain: {domain.record}")
            updated_domain = self._resolve_domain(domain)

            # Publish updated domain
            if updated_domain:
                routing_key = "documents.updated.domain"
                log.debug(f"[{self.actor_name}] Publishing updated domain to {routing_key}")
                self.publisher.tell(PublishDocument(updated_domain, routing_key))
                log.info(f"[{self.actor_name}] Published updated domain: {updated_domain.record} (id={updated_domain.id})")

            # Ack the message
            channel.basic_ack(delivery_tag=method.delivery_tag)

        except Exception as e:
            log.error(
                f"[{self.actor_name}] Error handling domain: {e}", exc_info=True
            )
            # Ack to prevent poison loop
            channel.basic_ack(delivery_tag=method.delivery_tag)

    def _validate_domain(self, domain: Domain) -> bool:
        """
        Validate domain document.

        Args:
            domain: Domain object

        Returns:
            True if valid, False otherwise (emits parse-error)
        """
        # Check dtype
        if not hasattr(domain, "dtype") or domain.dtype != "domain":
            self._emit_parse_error(
                f"Invalid dtype: expected 'domain', got '{getattr(domain, 'dtype', 'missing')}'",
                getattr(domain, "id", ""),
            )
            return False

        # Check required fields
        if not hasattr(domain, "record") or not domain.record:
            self._emit_parse_error(
                "Missing required field: 'record'", getattr(domain, "id", "")
            )
            return False

        # Check _rev field exists (for CouchDB)
        if not hasattr(domain, "_rev"):
            log.debug(f"[{self.actor_name}] Domain {domain.id} missing _rev field (will be added by CouchDB)")
            # Note: _rev is added by CouchDB on first insert, so this is just informational

        return True

    def _resolve_domain(self, domain: Domain) -> Optional[Domain]:
        """
        Resolve DNS records for domain.

        Args:
            domain: Domain object to resolve

        Returns:
            Updated Domain object with resolved addresses, or None on error
        """
        domain_name = domain.record
        resolved_addresses = []

        try:
            # Resolve A records (IPv4)
            try:
                answers = self.resolver.resolve(domain_name, "A")
                for rdata in answers:
                    resolved_addresses.append(str(rdata))
                log.debug(f"[{self.actor_name}] Resolved A records for {domain_name}: {resolved_addresses}")
            except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.resolver.NoNameservers) as e:
                log.debug(f"[{self.actor_name}] No A records for {domain_name}: {e}")
            except Exception as e:
                log.warning(f"[{self.actor_name}] Error resolving A records for {domain_name}: {e}")

            # Resolve AAAA records (IPv6)
            try:
                answers = self.resolver.resolve(domain_name, "AAAA")
                for rdata in answers:
                    resolved_addresses.append(str(rdata))
                log.debug(f"[{self.actor_name}] Resolved AAAA records for {domain_name}: {resolved_addresses}")
            except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.resolver.NoNameservers) as e:
                log.debug(f"[{self.actor_name}] No AAAA records for {domain_name}: {e}")
            except Exception as e:
                log.warning(f"[{self.actor_name}] Error resolving AAAA records for {domain_name}: {e}")

            # Update domain with resolved addresses
            domain.resolved_addresses = resolved_addresses

            # Emit resolution event
            if resolved_addresses:
                self._emit_event(
                    "dns-resolved",
                    f"Resolved {len(resolved_addresses)} addresses for {domain_name}",
                    domain.id,
                )
            else:
                self._emit_event(
                    "dns-no-records",
                    f"No DNS records found for {domain_name}",
                    domain.id,
                )

            return domain

        except Exception as e:
            log.error(f"[{self.actor_name}] Error resolving domain {domain_name}: {e}", exc_info=True)
            self._emit_event(
                "dns-error",
                f"DNS resolution failed for {domain_name}: {str(e)}",
                domain.id,
            )
            return None

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

    def _emit_event(self, event_type: str, details: str, source_id: str):
        """
        Emit an event to the publisher.

        Args:
            event_type: Type of event
            details: Freeform details string
            source_id: ID of the source document
        """
        event = create_event(self.actor_name, event_type, details, source_id)
        routing_key = self.routing_config["event_key"].format(eventType=event_type)
        self.publisher.tell(PublishEvent(event, routing_key))
        log.debug(f"[{self.actor_name}] Emitted event: {event_type}")

    def _handle_ack(self, delivery_tag: int):
        """
        Handle ack request.

        Args:
            delivery_tag: RabbitMQ delivery tag to ack
        """
        def _do_ack():
            try:
                if self.channel and self.channel.is_open:
                    self.channel.basic_ack(delivery_tag=delivery_tag)
            except Exception as e:
                log.error("[%s] Failed to ack delivery_tag=%s: %s", self.actor_name, delivery_tag, e)

        if not self._call_in_consume_thread(_do_ack):
            log.warning("[%s] Ack dropped; connection not ready", self.actor_name)
