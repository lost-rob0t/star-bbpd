from __future__ import annotations

import json
import os
import uuid

import pika
import pytest

from bbp.publisher_pool import document_properties
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


def _get(channel, queue):
    method, properties, body = channel.basic_get(queue=queue, auto_ack=True)
    assert method is not None
    return properties, json.loads(body)


def test_rabbitmq_routes_durable_new_update_and_persisted_domain_events():
    suffix = uuid.uuid4().hex
    connection = pika.BlockingConnection(
        pika.ConnectionParameters(host="127.0.0.1", port=5672, heartbeat=0)
    )
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
        channel.basic_publish(
            exchange="documents",
            routing_key="documents.ingest.domain",
            body=json.dumps(domain).encode(),
            properties=document_properties("domain"),
        )
        channel.basic_publish(
            exchange="documents",
            routing_key="documents.ingest.relation",
            body=json.dumps(relation).encode(),
            properties=document_properties("relation"),
        )
        channel.basic_publish(
            exchange="documents",
            routing_key="documents.updated.domain",
            body=json.dumps(domain).encode(),
            properties=document_properties("domain"),
        )
        channel.basic_publish(
            exchange="documents",
            routing_key="documents.new.domain",
            body=json.dumps({**domain, "_rev": "1-test"}).encode(),
            properties=document_properties("domain"),
        )

        ingest_properties, first = _get(channel, ingest_queue)
        relation_properties, second = _get(channel, ingest_queue)
        update_properties, update = _get(channel, update_queue)
        new_properties, persisted = _get(channel, dns_queue)

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
