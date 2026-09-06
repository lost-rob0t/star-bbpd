from __future__ import annotations

from bbp.topology import build_topology, declare_topology


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


class CompatOnlyConfig:
    """Legacy deployment: no canonical dispatch template configured."""

    documents = {"exchange": "documents", "exchange_type": "topic"}
    events = {"exchange": "events", "exchange_type": "topic"}
    routing = {
        "target_key": "actors.{actor}.new.target",
        "target_dispatch_key": None,
        "document_new_key": "documents.new.{dtype}",
    }
    data = {}

    def enabled_actors(self):
        return ["subfinder"]

    def get_actor_config(self, actor):
        return {"queue": f"bbp.{actor}.targets"}


class DualBindingConfig(CompatOnlyConfig):
    """Current starintel-server deployment: canonical + compatibility keys."""

    routing = {
        "target_key": "actors.{actor}.new.target",
        "target_dispatch_key": "documents.target.dispatch.{actor}",
        "document_new_key": "documents.new.{dtype}",
    }


def test_legacy_config_binds_only_compat_key():
    plan = build_topology(CompatOnlyConfig())
    assert [(b.queue, b.routing_key) for b in plan.bindings] == [
        ("bbp.subfinder.targets", "actors.subfinder.new.target"),
    ]


def test_current_server_config_binds_canonical_and_compat_keys():
    plan = build_topology(DualBindingConfig())
    assert [(b.queue, b.routing_key) for b in plan.bindings] == [
        ("bbp.subfinder.targets", "actors.subfinder.new.target"),
        ("bbp.subfinder.targets", "documents.target.dispatch.subfinder"),
    ]


def test_both_keys_bind_to_the_same_durable_queue():
    channel = FakeChannel()
    declare_topology(channel, build_topology(DualBindingConfig()))

    assert channel.queues == [
        {
            "queue": "bbp.subfinder.targets",
            "durable": True,
            "exclusive": False,
            "auto_delete": False,
        }
    ]
    assert [(b["routing_key"]) for b in channel.bindings] == [
        "actors.subfinder.new.target",
        "documents.target.dispatch.subfinder",
    ]
