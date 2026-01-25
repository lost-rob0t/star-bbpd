#!/usr/bin/env python3
"""
Manual test for DNS resolver integration.

This script:
1. Publishes a test domain document to documents.new.domain
2. Waits for DNS resolver to process it
3. Verifies the updated domain appears on documents.updated.domain
"""

import logging
import sys
import time
import pika
from starintel_doc.hosts import Domain

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


def publish_test_domain(rabbit_host: str = "bots.star.intel"):
    """Publish a test domain document"""

    # Create test domain
    domain = Domain(
        dtype="domain",
        record="example.com",
        id="test-domain-001",
        _rev="1-test"
    )

    log.info(f"Publishing test domain: {domain.record}")

    # Connect to RabbitMQ
    params = pika.ConnectionParameters(
        host=rabbit_host,
        port=5672,
        virtual_host="/",
        credentials=pika.PlainCredentials("guest", "guest")
    )

    connection = pika.BlockingConnection(params)
    channel = connection.channel()

    # Publish to documents.new.domain
    routing_key = "documents.new.domain"
    exchange = "documents"

    channel.basic_publish(
        exchange=exchange,
        routing_key=routing_key,
        body=domain.to_json(),
        properties=pika.BasicProperties(
            delivery_mode=2,  # persistent
            content_type="application/json"
        )
    )

    log.info(f"Published to {exchange}/{routing_key}")

    connection.close()
    log.info("Done. Check logs to verify DNS resolver processed the domain.")


def consume_updated_domain(rabbit_host: str = "bots.star.intel", timeout: int = 10):
    """Consume and verify updated domain"""

    log.info("Setting up consumer for documents.updated.domain...")

    # Connect to RabbitMQ
    params = pika.ConnectionParameters(
        host=rabbit_host,
        port=5672,
        virtual_host="/",
        credentials=pika.PlainCredentials("guest", "guest")
    )

    connection = pika.BlockingConnection(params)
    channel = connection.channel()

    # Declare a temporary queue and bind to documents.updated.domain
    result = channel.queue_declare(queue="", exclusive=True)
    queue_name = result.method.queue

    channel.queue_bind(
        queue=queue_name,
        exchange="documents",
        routing_key="documents.updated.domain"
    )

    log.info(f"Listening on queue {queue_name} for {timeout} seconds...")

    received = []

    def callback(ch, method, _properties, body):
        log.info(f"Received message: {body[:100]}...")
        try:
            domain = Domain.from_json(body.decode('utf-8'))
            log.info(f"Domain: {domain.record}, resolved_addresses: {getattr(domain, 'resolved_addresses', [])}")
            received.append(domain)
            ch.basic_ack(delivery_tag=method.delivery_tag)
            ch.stop_consuming()
        except Exception as e:
            log.error(f"Error parsing domain: {e}")
            ch.basic_ack(delivery_tag=method.delivery_tag)

    channel.basic_consume(queue=queue_name, on_message_callback=callback, auto_ack=False)

    # Consume with timeout (use polling approach)
    start_time = time.time()
    while time.time() - start_time < timeout:
        connection.process_data_events(time_limit=1)
        if received:
            break


    connection.close()

    if received:
        log.info(f"SUCCESS: Received {len(received)} updated domain(s)")
        for domain in received:
            log.info(f"  - {domain.record}: {getattr(domain, 'resolved_addresses', [])}")
        return True
    else:
        log.warning("TIMEOUT: No updated domain received")
        return False


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "consume":
        # Just consume
        consume_updated_domain()
    elif len(sys.argv) > 1 and sys.argv[1] == "publish":
        # Just publish
        publish_test_domain()
    else:
        # Full test: publish then consume
        log.info("Running full test: publish -> consume")
        publish_test_domain()
        time.sleep(2)  # Give resolver time to process
        success = consume_updated_domain(timeout=10)
        sys.exit(0 if success else 1)
