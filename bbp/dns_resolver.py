#!/usr/bin/env python3

import logging
import threading
from typing import Any, Dict, Optional

import dns.resolver
import pika
import pykka

from bbp.events import create_event
from bbp.publisher_pool import PublishDocument, PublishEvent
from starintel_doc.hosts import Domain

log = logging.getLogger(__name__)


class DnsResolverActor(pykka.ThreadingActor):
    """Resolve persisted domain documents and publish document updates."""

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
        self.consume_thread: Optional[threading.Thread] = None
        self.connection: Optional[pika.BlockingConnection] = None
        self.channel = None
        self._stop_consuming = False

        self.timeout = resolver_config.get("timeout_sec", 5)
        self.resolver = dns.resolver.Resolver()
        nameservers = resolver_config.get("nameservers")
        if nameservers:
            self.resolver.nameservers = nameservers
        self.resolver.timeout = self.timeout
        self.resolver.lifetime = self.timeout

    def on_start(self):
        self._stop_consuming = False
        self.consume_thread = threading.Thread(
            target=self._consume_loop,
            daemon=True,
            name="dns-resolver-consumer",
        )
        self.consume_thread.start()

    def on_stop(self):
        self._stop_consuming = True
        if self.connection and self.connection.is_open:
            try:
                self.connection.add_callback_threadsafe(self._stop_channel)
            except Exception as exc:
                log.warning("[dns-resolver] Failed to schedule consumer stop: %s", exc)

        if self.consume_thread and self.consume_thread.is_alive():
            self.consume_thread.join(timeout=5)

        if self.connection and self.connection.is_open:
            self.connection.close()

    def _stop_channel(self) -> None:
        if self.channel and self.channel.is_open:
            self.channel.stop_consuming()

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
            self.connection = pika.BlockingConnection(params)
            self.channel = self.connection.channel()
            self.channel.basic_qos(prefetch_count=self.prefetch)
            self.channel.basic_consume(
                queue=self.queue_name,
                on_message_callback=self._handle_domain,
                auto_ack=False,
            )
            self.channel.start_consuming()
        except Exception as exc:
            if not self._stop_consuming:
                log.error("[dns-resolver] Consumer failed: %s", exc, exc_info=True)
        finally:
            if self.connection and self.connection.is_open:
                self.connection.close()

    def _handle_domain(self, channel, method, _properties, body):
        try:
            body_str = body.decode("utf-8")
            try:
                domain = Domain.from_json(body_str)
            except Exception as exc:
                self._emit_parse_error(f"Failed to parse JSON: {exc}", "")
                channel.basic_ack(delivery_tag=method.delivery_tag)
                return

            if not self._validate_domain(domain):
                channel.basic_ack(delivery_tag=method.delivery_tag)
                return

            updated_domain = self._resolve_domain(domain)
            if updated_domain:
                routing_key = self.routing_config["document_update_key"].format(
                    dtype="domain"
                )
                self.publisher.tell(PublishDocument(updated_domain, routing_key))

            channel.basic_ack(delivery_tag=method.delivery_tag)
        except Exception as exc:
            log.error("[dns-resolver] Domain handling failed: %s", exc, exc_info=True)
            channel.basic_ack(delivery_tag=method.delivery_tag)

    def _validate_domain(self, domain: Domain) -> bool:
        if getattr(domain, "dtype", None) != "domain":
            self._emit_parse_error(
                f"Invalid dtype: expected 'domain', got {getattr(domain, 'dtype', 'missing')!r}",
                getattr(domain, "id", ""),
            )
            return False
        if not getattr(domain, "record", None):
            self._emit_parse_error(
                "Missing required field: 'record'", getattr(domain, "id", "")
            )
            return False
        return True

    def _resolve_domain(self, domain: Domain) -> Optional[Domain]:
        addresses = []
        try:
            for record_type in ("A", "AAAA"):
                try:
                    addresses.extend(
                        str(value)
                        for value in self.resolver.resolve(domain.record, record_type)
                    )
                except (
                    dns.resolver.NXDOMAIN,
                    dns.resolver.NoAnswer,
                    dns.resolver.NoNameservers,
                ):
                    continue

            domain.resolved_addresses = addresses
            event_type = "dns-resolved" if addresses else "dns-no-records"
            details = (
                f"Resolved {len(addresses)} addresses for {domain.record}"
                if addresses
                else f"No DNS records found for {domain.record}"
            )
            self._emit_event(event_type, details, domain.id)
            return domain
        except Exception as exc:
            self._emit_event(
                "dns-error",
                f"DNS resolution failed for {domain.record}: {exc}",
                domain.id,
            )
            return None

    def _emit_parse_error(self, details: str, source_id: str):
        self._emit_event("parse-error", details, source_id)

    def _emit_event(self, event_type: str, details: str, source_id: str):
        event = create_event(self.actor_name, event_type, details, source_id)
        routing_key = self.routing_config["event_key"].format(eventType=event_type)
        self.publisher.tell(PublishEvent(event, routing_key))
