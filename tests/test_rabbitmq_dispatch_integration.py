from __future__ import annotations

import json
import os
import uuid

import pika
import pytest

from bbp.config import Config
from bbp.topology import build_topology, declare_topology

pytestmark = pytest.mark.skipif(
    os.environ.get("BBPD_RABBITMQ_INTEGRATION") != "1",
    reason="RabbitMQ integration is disabled",
)


def _connection():
    return pika.BlockingConnection(
        pika.ConnectionParameters(host="127.0.0.1", port=5672, heartbeat=0)
    )


def _target_payload(actor: str, target: str) -> str:
    # Minimal legacy-shaped target body; the broker test only asserts that
    # both server routing generations deliver to the BBPD queue.
    return json.dumps({"dtype": "target", "_id": f"target:{target}", "data": {"actor": actor, "target": target}})


def test_both_target_dispatch_generations_deliver_to_same_queue():
    suffix = uuid.uuid4().hex
    config = Config.load(os.path.join(os.path.dirname(__file__), "..", "config.test.toml"))
    queue = f"bbp.test.{suffix}.subfinder.targets"
    config.data["actors"] = {"subfinder": {"enabled": True, "queue": queue}}
    config.data["routing"]["target_dispatch_key"] = (
        f"documents.target.dispatch.{suffix}.{{actor}}"
    )
    config.data["routing"]["target_key"] = f"actors.{suffix}.{{actor}}.new.target"

    connection = _connection()
    channel = connection.channel()
    try:
        declare_topology(channel, build_topology(config))
        exchange = config.documents["exchange"]

        channel.basic_publish(
            exchange=exchange,
            routing_key=f"documents.target.dispatch.{suffix}.subfinder",
            body=_target_payload("subfinder", "legacy.example"),
            properties=pika.BasicProperties(content_type="application/json", delivery_mode=2),
        )
        channel.basic_publish(
            exchange=exchange,
            routing_key=f"actors.{suffix}.subfinder.new.target",
            body=_target_payload("subfinder", "modern.example"),
            properties=pika.BasicProperties(content_type="application/json", delivery_mode=2),
        )

        connection.process_data_events(time_limit=2)

        received = []
        for _ in range(2):
            method, _properties, body = channel.basic_get(queue=queue, auto_ack=True)
            assert method is not None, "expected a target on the BBPD queue"
            received.append(json.loads(body)["data"]["target"])
        assert sorted(received) == ["legacy.example", "modern.example"]
    finally:
        try:
            channel.queue_delete(queue=queue)
        finally:
            connection.close()
