#!/usr/bin/env python3
"""Manual local DNS resolver integration helper."""

import logging
import os
import sys
import time

import pika
from starintel_doc.hosts import Domain

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


def rabbit_parameters(host: str | None = None) -> pika.ConnectionParameters:
    return pika.ConnectionParameters(
        host=host or os.environ.get("BBP_RABBIT_HOST", "localhost"),
        port=int(os.environ.get("BBP_RABBIT_PORT", "5672")),
        virtual_host=os.environ.get("BBP_RABBIT_VHOST", "/"),
        credentials=pika.PlainCredentials(
            os.environ.get("BBP_RABBIT_USERNAME", "bbp-local"),
            os.environ.get("BBP_RABBIT_PASSWORD", "replace-me"),
        ),
    )


def publish_test_domain(rabbit_host: str | None = None) -> None:
    domain = Domain(
        dtype="domain",
        record="example.com",
        id="test-domain-001",
        rev="1-test",
    )

    connection = pika.BlockingConnection(rabbit_parameters(rabbit_host))
    try:
        channel = connection.channel()
        channel.basic_publish(
            exchange="documents",
            routing_key="documents.new.domain",
            body=domain.to_json(),
            properties=pika.BasicProperties(
                delivery_mode=2,
                content_type="application/json",
            ),
        )
        log.info("Published test domain %s", domain.record)
    finally:
        connection.close()


def consume_updated_domain(
    rabbit_host: str | None = None,
    timeout: int = 10,
) -> bool:
    connection = pika.BlockingConnection(rabbit_parameters(rabbit_host))
    channel = connection.channel()
    result = channel.queue_declare(queue="", exclusive=True)
    queue_name = result.method.queue
    channel.queue_bind(
        queue=queue_name,
        exchange="documents",
        routing_key="documents.updated.domain",
    )

    received = []

    def callback(ch, method, _properties, body):
        try:
            received.append(Domain.from_json(body.decode("utf-8")))
        finally:
            ch.basic_ack(delivery_tag=method.delivery_tag)

    channel.basic_consume(
        queue=queue_name,
        on_message_callback=callback,
        auto_ack=False,
    )

    started = time.monotonic()
    while time.monotonic() - started < timeout and not received:
        connection.process_data_events(time_limit=1)

    connection.close()
    if not received:
        log.warning("No updated domain received before timeout")
        return False

    for domain in received:
        log.info(
            "Received %s -> %s",
            domain.record,
            getattr(domain, "resolved_addresses", []),
        )
    return True


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "full"
    if command == "publish":
        publish_test_domain()
        raise SystemExit(0)
    if command == "consume":
        raise SystemExit(0 if consume_updated_domain() else 1)

    publish_test_domain()
    time.sleep(2)
    raise SystemExit(0 if consume_updated_domain() else 1)
