from __future__ import annotations

import json
import os
import uuid

import pika
import pytest

from bbp.document_boundary import build_domain, build_relation, build_target
from bbp.publisher_pool import PublishDocument, PublishRelation, PublisherWorker
from bbp.rabbit import RabbitManager

pytestmark = pytest.mark.skipif(
    os.environ.get("BBPD_RABBITMQ_INTEGRATION") != "1",
    reason="RabbitMQ identity integration is disabled",
)

NOW = "2026-08-05T12:00:00Z"


def connection():
    return pika.BlockingConnection(
        pika.ConnectionParameters(host="127.0.0.1", port=5672, heartbeat=0)
    )


def test_replay_after_confirm_before_ack_reuses_document_and_relation_ids():
    suffix = uuid.uuid4().hex
    queue = f"bbp.test.{suffix}.identity"
    routing = f"documents.ingest.identity-{suffix}"
    observer = connection()
    channel = observer.channel()
    channel.exchange_declare(exchange="documents", exchange_type="topic", durable=True)
    channel.exchange_declare(exchange="events", exchange_type="topic", durable=True)
    channel.queue_declare(queue=queue, durable=False, auto_delete=True)
    channel.queue_bind(queue=queue, exchange="documents", routing_key=routing)

    manager = RabbitManager(
        {
            "host": "127.0.0.1",
            "port": 5672,
            "vhost": "/",
            "username": "guest",
            "password": "guest",
            "heartbeat_sec": 0,
        }
    )
    worker = PublisherWorker.start(
        manager,
        {
            "documents": {"exchange": "documents"},
            "events": {"exchange": "events"},
            "service": {
                "publisher_max_retries": 1,
                "publisher_retry_delays_sec": [0],
            },
        },
        0,
    )

    target = build_target("subfinder", "example.com", "scan-a", now=NOW)
    first_domain = build_domain("API.Example.com.", target, "subfinder")
    first_relation = build_relation(
        target.id,
        first_domain.id,
        "related-to",
        "subfinder discovery",
        target,
        "subfinder",
    )
    replay_domain = build_domain("api.example.com", target, "subfinder")
    replay_relation = build_relation(
        target.id,
        replay_domain.id,
        "related-to",
        "subfinder replay",
        target,
        "subfinder",
    )

    try:
        messages = [
            PublishDocument(first_domain, routing, "run-1"),
            PublishRelation(first_relation, routing, "run-1"),
            PublishDocument(replay_domain, routing, "run-1"),
            PublishRelation(replay_relation, routing, "run-1"),
        ]
        for message in messages:
            result = worker.ask(message, block=True, timeout=10)
            assert result.ok is True

        payloads = []
        for _ in messages:
            method, _properties, body = channel.basic_get(queue=queue, auto_ack=True)
            assert method is not None
            payloads.append(json.loads(body))

        domains = [value for value in payloads if value["dtype"] == "domain"]
        relations = [value for value in payloads if value["dtype"] == "relation"]
        assert domains[0] == domains[1]
        assert domains[0]["_id"] == first_domain.id == replay_domain.id
        assert relations[0]["_id"] == relations[1]["_id"]
        assert relations[0]["_id"] == first_relation.id == replay_relation.id
    finally:
        worker.stop(block=True, timeout=10)
        channel.queue_delete(queue=queue)
        observer.close()
