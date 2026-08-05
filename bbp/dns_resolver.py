#!/usr/bin/env python3

from __future__ import annotations

import logging
import threading
from hashlib import sha256
from typing import Any, Dict

import dns.resolver
import pika
import pykka

from bbp.determinism import RecursionRejected, validate_recursion
from bbp.document_boundary import (
    CanonicalDocument,
    DocumentBoundaryError,
    decode_domain,
    domain_name,
    with_domain_addresses,
)
from bbp.events import create_event
from bbp.publisher_pool import PublishDocument, PublishEvent
from bbp.settlement import FailureKind, PublishResult, failure_decision

log = logging.getLogger(__name__)


class DnsResolverActor(pykka.ThreadingActor):
    """Resolve persisted domains and ACK only after the update is confirmed."""

    def __init__(
        self,
        rabbit_config: Dict[str, Any],
        publisher_ref: Any,
        resolver_config: Dict[str, Any],
        routing_config: Dict[str, Any],
    ) -> None:
        super().__init__()
        if not callable(getattr(publisher_ref, "publish_required", None)):
            raise TypeError("publisher must provide publish_required(message, timeout)")
        self.actor_name = "dns-resolver"
        self.rabbit_config = rabbit_config
        self.publisher = publisher_ref
        self.routing_config = routing_config
        self.queue_name = resolver_config.get("queue", "bbp.dns-resolver.domains")
        self.prefetch = resolver_config.get("prefetch", 5)
        self.publish_timeout = float(resolver_config.get("publish_timeout_sec", 30))
        self.shutdown_timeout = float(resolver_config.get("shutdown_timeout_sec", 60))
        self.timeout = resolver_config.get("timeout_sec", 5)
        self.consume_thread: threading.Thread | None = None
        self.connection: pika.BlockingConnection | None = None
        self.channel = None
        self._stop_consuming = False
        self._conn_lock = threading.Lock()

        self.resolver = dns.resolver.Resolver()
        nameservers = resolver_config.get("nameservers")
        if nameservers:
            self.resolver.nameservers = nameservers
        self.resolver.timeout = self.timeout
        self.resolver.lifetime = self.timeout

    def on_start(self) -> None:
        self._stop_consuming = False
        self.consume_thread = threading.Thread(
            target=self._consume_loop,
            daemon=True,
            name="dns-resolver-consumer",
        )
        self.consume_thread.start()

    def on_stop(self) -> None:
        self._stop_consuming = True

        def stop() -> None:
            if self.channel and self.channel.is_open:
                self.channel.stop_consuming()

        with self._conn_lock:
            connection = self.connection
        if connection and connection.is_open:
            connection.add_callback_threadsafe(stop)

        thread = self.consume_thread
        if thread and thread.is_alive():
            thread.join(timeout=self.shutdown_timeout)
        if thread and thread.is_alive():
            raise RuntimeError("dns-resolver consumer did not stop cleanly")

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
            if self._stop_consuming:
                return
            channel.basic_qos(prefetch_count=self.prefetch)
            channel.basic_consume(
                queue=self.queue_name,
                on_message_callback=self._handle_domain,
                auto_ack=False,
            )
            channel.start_consuming()
        except Exception as exc:
            if not self._stop_consuming:
                log.error("[dns-resolver] consumer failed: %s", exc, exc_info=True)
        finally:
            if connection and connection.is_open:
                connection.close()
            with self._conn_lock:
                self.connection = None
                self.channel = None

    def _handle_domain(self, channel, method, _properties, body) -> None:
        run_id = self._raw_run_id(body)
        redelivered = bool(getattr(method, "redelivered", False))
        domain: CanonicalDocument | None = None
        try:
            try:
                domain = decode_domain(body)
            except DocumentBoundaryError as exc:
                self._emit_event(
                    "parse-error",
                    f"run_id={run_id}; category={exc.category}; disposition=ack",
                    "",
                    run_id,
                )
                channel.basic_ack(delivery_tag=method.delivery_tag)
                return

            try:
                state = validate_recursion(domain.to_dict(), self.actor_name)
            except RecursionRejected as exc:
                self._emit_event(
                    "dns-rejected",
                    (
                        f"run_id={run_id}; category={exc.category}; "
                        "decision=permanent-no-requeue"
                    ),
                    domain.id,
                    run_id,
                )
                channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
                return

            run_id = state.run_id
            updated = self._resolve_domain(domain, run_id)
            routing_key = self.routing_config["document_update_key"].format(
                dtype="domain"
            )
            result = self.publisher.publish_required(
                PublishDocument(updated, routing_key, run_id),
                self.publish_timeout,
            )
            if not isinstance(result, PublishResult):
                result = PublishResult.failure(
                    routing_key,
                    0,
                    FailureKind.PERMANENT,
                    f"invalid settlement result: {type(result)!r}",
                )
            if result.ok:
                channel.basic_ack(delivery_tag=method.delivery_tag)
                return

            decision = failure_decision(
                result.failure_kind or FailureKind.TRANSIENT,
                redelivered=redelivered,
            )
            self._emit_event(
                "dns-error",
                (
                    f"run_id={run_id}; update publish failed; "
                    f"decision={decision.reason}; error={result.error}"
                ),
                domain.id,
                run_id,
            )
            channel.basic_nack(
                delivery_tag=method.delivery_tag,
                requeue=decision.requeue,
            )
        except RecursionRejected as exc:
            self._emit_event(
                "dns-rejected",
                (
                    f"run_id={run_id}; category={exc.category}; "
                    "decision=permanent-no-requeue"
                ),
                domain.id if domain else "",
                run_id,
            )
            channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
        except Exception as exc:
            decision = failure_decision(
                FailureKind.TRANSIENT,
                redelivered=redelivered,
            )
            self._emit_event(
                "dns-error",
                f"run_id={run_id}; decision={decision.reason}; error={exc}",
                domain.id if domain else "",
                run_id,
            )
            channel.basic_nack(
                delivery_tag=method.delivery_tag,
                requeue=decision.requeue,
            )

    def _resolve_domain(
        self,
        domain: CanonicalDocument,
        run_id: str,
    ) -> CanonicalDocument:
        name = domain_name(domain)
        addresses: list[str] = []
        for record_type in ("A", "AAAA"):
            try:
                addresses.extend(
                    str(value) for value in self.resolver.resolve(name, record_type)
                )
            except (
                dns.resolver.NXDOMAIN,
                dns.resolver.NoAnswer,
                dns.resolver.NoNameservers,
            ):
                continue

        updated = with_domain_addresses(domain, addresses)
        event_type = "dns-resolved" if addresses else "dns-no-records"
        details = (
            f"run_id={run_id}; resolved_addresses={len(addresses)}"
            if addresses
            else f"run_id={run_id}; no DNS records found"
        )
        self._emit_event(event_type, details, domain.id, run_id)
        return updated

    @staticmethod
    def _raw_run_id(body: bytes | str) -> str:
        payload = body.encode("utf-8") if isinstance(body, str) else body
        return f"run:{sha256(payload).hexdigest()}"

    def _emit_event(
        self,
        event_type: str,
        details: str,
        source_id: str,
        run_id: str,
    ) -> None:
        event = create_event(self.actor_name, event_type, details, source_id)
        routing_key = self.routing_config["event_key"].format(eventType=event_type)
        try:
            self.publisher.tell(PublishEvent(event, routing_key, run_id))
        except Exception as exc:
            log.warning(
                "[dns-resolver] run_id=%s best-effort event %s failed: %s",
                run_id,
                event_type,
                exc,
            )
