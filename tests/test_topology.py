from __future__ import annotations

from bbp.topology import build_topology, declare_topology


class FakeConfig:
    documents = {"exchange": "documents", "exchange_type": "topic"}
    events = {"exchange": "events", "exchange_type": "topic"}
    routing = {
        "target_key": "actors.{actor}.new.target",
        "document_new_key": "documents.new.{dtype}",
    }
    data = {
        "dns_resolver": {
            "enabled": True,
            "queue": "bbp.dns-resolver.domains",
        }
    }

    def enabled_actors(self):
        return ["subfinder", "nmap"]

    def get_actor_config(self, actor):
        return {"queue": f"bbp.{actor}.targets"}


class FakeChannel:
    def __init__(self):
        self.exchanges = []
        self.queues = []
        self.bindings = []

    def exchange_declare(self, **kwargs):
        self.exchanges.append(kwargs)

    def queue_declare(self, **kwargs):
        self.queues.append(kwargs)

    def queue_bind(self, **kwargs):
        self.bindings.append(kwargs)


def test_topology_plan_centralizes_actor_and_dns_bindings():
    plan = build_topology(FakeConfig())

    assert [exchange.name for exchange in plan.exchanges] == ["documents", "events"]
    assert [(item.queue, item.routing_key) for item in plan.bindings] == [
        ("bbp.subfinder.targets", "actors.subfinder.new.target"),
        ("bbp.nmap.targets", "actors.nmap.new.target"),
        ("bbp.dns-resolver.domains", "documents.new.domain"),
    ]


def test_topology_declaration_is_durable_and_non_ephemeral():
    channel = FakeChannel()
    declare_topology(channel, build_topology(FakeConfig()))

    assert channel.exchanges == [
        {"exchange": "documents", "exchange_type": "topic", "durable": True},
        {"exchange": "events", "exchange_type": "topic", "durable": True},
    ]
    assert all(
        queue == {
            "queue": queue["queue"],
            "durable": True,
            "exclusive": False,
            "auto_delete": False,
        }
        for queue in channel.queues
    )
    assert channel.bindings[-1] == {
        "queue": "bbp.dns-resolver.domains",
        "exchange": "documents",
        "routing_key": "documents.new.domain",
    }
