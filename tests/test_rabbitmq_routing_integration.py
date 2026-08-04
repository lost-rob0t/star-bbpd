from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass

import pika
import pytest

from bbp.publisher_pool import PublishDocument, PublisherWorker, document_properties
from bbp.rabbit import RabbitManager
from bbp.settlement import FailureKind
from bbp.topology import build_topology, declare_topology

pytestmark = pytest.mark.skipif(
    os.environ.get("BBPD_RABBITMQ_INTEGRATION") != "1",
    reason="RabbitMQ integration is disabled",
)


class IntegrationConfig:
    documents = {"exchange": "documents", "exchange_type": "topic"}
    events = {"exchange": "events", "exchange_type": "topic"}
    routing = {
        "target_key": "actors.{actor}.new.target",
        "document_new_key": "documents.new.{dtype}",
    }

    def __init__(self, suffix: str):
        self.data = {
            "dns_resolver": {
                "enabled": True,
                "queue": f"bbp.test.{suffix}.dns",
            }
        }

    def enabled_actors(self):
        return []

    def get_actor_config(self, actor):
        raise AssertionError(actor)


@dataclass
class WireDoc:
    dtype: str
    id: str

    def to_json(self):
        return json.dumps({"dtype": self.dtype, "_id": self.id})


def _connection():
    return pika.BlockingConnection(
        pika.ConnectionParameters(host="127.0.0.1", port=5672, heartbeat=0)
    )


def _get(channel, queue, *, auto_ack=True):
    method, properties, body = channel.basic_get(queue=queue, auto_ack=auto_ack)
    assert method is not None
    return method, properties, json.loads(body)


def test_rabbitmq_routes_durable_new_update_and_persisted_domain_events():
    suffix = uuid.uuid4().hex
    connection = _connection()
    channel = connection.channel()

    config = IntegrationConfig(suffix)
    plan = build_topology(config)
    dns_queue = plan.bindings[0].queue
    ingest_queue = f"bbp.test.{suffix}.ingest"
    update_queue = f"bbp.test.{suffix}.updates"

    try:
        declare_topology(channel, plan)
        channel.queue_declare(
            queue=ingest_queue, durable=False, exclusive=False, auto_delete=True
        )
        channel.queue_bind(
            queue=ingest_queue,
            exchange="documents",
            routing_key="documents.ingest.#",
        )
        channel.queue_declare(
            queue=update_queue, durable=False, exclusive=False, auto_delete=True
        )
        channel.queue_bind(
            queue=update_queue,
            exchange="documents",
            routing_key="documents.updated.#",
        )

        domain = {"dtype": "domain", "_id": "domain-1"}
        relation = {"dtype": "relation", "_id": "relation-1"}
        for key, value in (
            ("documents.ingest.domain", domain),
            ("documents.ingest.relation", relation),
            ("documents.updated.domain", domain),
            ("documents.new.domain", {**domain, "_rev": "1-test"}),
        ):
            channel.basic_publish(
                exchange="documents",
                routing_key=key,
                body=json.dumps(value).encode(),
                properties=document_properties(value["dtype"]),
            )

        _, ingest_properties, first = _get(channel, ingest_queue)
        _, relation_properties, second = _get(channel, ingest_queue)
        _, update_properties, update = _get(channel, update_queue)
        _, new_properties, persisted = _get(channel, dns_queue)

        assert [first["dtype"], second["dtype"]] == ["domain", "relation"]
        assert ingest_properties.type == "domain"
        assert relation_properties.type == "relation"
        assert update["dtype"] == update_properties.type == "domain"
        assert persisted["_rev"] == "1-test"
        assert new_properties.content_type == "application/json"
        assert new_properties.content_encoding == "utf-8"
    finally:
        for queue in (dns_queue, ingest_queue, update_queue):
            try:
                channel.queue_delete(queue=queue)
            except Exception:
                pass
        connection.close()


def test_publisher_worker_confirms_success_and_detects_unroutable_output():
    suffix = uuid.uuid4().hex
    queue = f"bbp.test.{suffix}.confirmed"
    connection = _connection()
    channel = connection.channel()
    channel.exchange_declare(exchange="documents", exchange_type="topic", durable=True)
    channel.exchange_declare(exchange="events", exchange_type="topic", durable=True)
    channel.queue_declare(queue=queue, durable=False, auto_delete=True)
    channel.queue_bind(
        queue=queue,
        exchange="documents",
        routing_key=f"documents.ingest.confirmed-{suffix}",
    )

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

    try:
        success = worker.ask(
            PublishDocument(
                WireDoc("domain", "domain-confirmed"),
                f"documents.ingest.confirmed-{suffix}",
                "run-confirmed",
            ),
            block=True,
            timeout=10,
        )
        assert success.ok is True
        assert success.attempts == 1
        _, properties, body = _get(channel, queue)
        assert body["_id"] == "domain-confirmed"
        assert properties.type == "domain"

        unroutable = worker.ask(
            PublishDocument(
                WireDoc("domain", "domain-unroutable"),
                f"unbound.{suffix}",
                "run-unroutable",
            ),
            block=True,
            timeout=10,
        )
        assert unroutable.ok is False
        assert unroutable.failure_kind is FailureKind.PERMANENT
        assert "unroutable" in unroutable.error
    finally:
        worker.stop(block=True, timeout=10)
        channel.queue_delete(queue=queue)
        connection.close()


def test_rabbitmq_requeue_then_reject_is_bounded():
    suffix = uuid.uuid4().hex
    queue = f"bbp.test.{suffix}.delivery"
    connection = _connection()
    channel = connection.channel()
    channel.queue_declare(queue=queue, durable=False, auto_delete=True)
    channel.basic_publish(
        exchange="",
        routing_key=queue,
        body=json.dumps({"target": "test"}).encode(),
    )

    try:
        first, _, _ = _get(channel, queue, auto_ack=False)
        assert first.redelivered is False
        channel.basic_nack(delivery_tag=first.delivery_tag, requeue=True)

        second, _, _ = _get(channel, queue, auto_ack=False)
        assert second.redelivered is True
        channel.basic_nack(delivery_tag=second.delivery_tag, requeue=False)

        method, _, _ = channel.basic_get(queue=queue, auto_ack=True)
        assert method is None
    finally:
        channel.queue_delete(queue=queue)
        connection.close()
