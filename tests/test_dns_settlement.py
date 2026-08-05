from types import SimpleNamespace

from bbp.document_boundary import DocumentBoundaryError
from bbp.dns_resolver import DnsResolverActor
from bbp.settlement import FailureKind, PublishResult

NOW = "2026-08-05T12:00:00Z"


class FakePublisher:
    def __init__(self, result):
        self.result = result
        self.required = []
        self.events = []

    def publish_required(self, message, timeout=None):
        self.required.append((message, timeout))
        return self.result

    def tell(self, message):
        self.events.append(message)


class FakeChannel:
    def __init__(self):
        self.acks = []
        self.nacks = []

    def basic_ack(self, **kwargs):
        self.acks.append(kwargs)

    def basic_nack(self, **kwargs):
        self.nacks.append(kwargs)


class FakeResolver:
    def __init__(self):
        self.nameservers = []
        self.timeout = 0
        self.lifetime = 0


class FakeDomain:
    id = "domain-1"

    def __init__(self, extensions=None):
        self.extensions = extensions or {}

    def to_dict(self):
        return {
            "_id": self.id,
            "date_added": NOW,
            "date_updated": NOW,
            "extensions": self.extensions,
        }


def actor(result, monkeypatch):
    monkeypatch.setattr(
        "bbp.dns_resolver.dns.resolver.Resolver",
        FakeResolver,
    )
    return DnsResolverActor(
        {},
        FakePublisher(result),
        {},
        {
            "document_update_key": "documents.updated.{dtype}",
            "event_key": "event.{eventType}",
        },
    )


def test_dns_ack_waits_for_confirmed_update(monkeypatch):
    value = actor(
        PublishResult.success("documents.updated.domain", 1),
        monkeypatch,
    )
    monkeypatch.setattr("bbp.dns_resolver.decode_domain", lambda body: FakeDomain())
    monkeypatch.setattr(value, "_resolve_domain", lambda domain, run_id: domain)
    channel = FakeChannel()

    value._handle_domain(
        channel,
        SimpleNamespace(delivery_tag=1, redelivered=False),
        None,
        b"{}",
    )

    assert channel.acks == [{"delivery_tag": 1}]
    assert channel.nacks == []


def test_dns_transient_update_failure_requeues_once(monkeypatch):
    result = PublishResult.failure(
        "documents.updated.domain", 3, FailureKind.TRANSIENT, "lost"
    )
    value = actor(result, monkeypatch)
    monkeypatch.setattr("bbp.dns_resolver.decode_domain", lambda body: FakeDomain())
    monkeypatch.setattr(value, "_resolve_domain", lambda domain, run_id: domain)
    channel = FakeChannel()

    value._handle_domain(
        channel,
        SimpleNamespace(delivery_tag=2, redelivered=False),
        None,
        b"{}",
    )

    assert channel.acks == []
    assert channel.nacks == [{"delivery_tag": 2, "requeue": True}]


def test_dns_redelivery_failure_is_rejected(monkeypatch):
    result = PublishResult.failure(
        "documents.updated.domain", 3, FailureKind.TRANSIENT, "lost"
    )
    value = actor(result, monkeypatch)
    monkeypatch.setattr("bbp.dns_resolver.decode_domain", lambda body: FakeDomain())
    monkeypatch.setattr(value, "_resolve_domain", lambda domain, run_id: domain)
    channel = FakeChannel()

    value._handle_domain(
        channel,
        SimpleNamespace(delivery_tag=3, redelivered=True),
        None,
        b"{}",
    )

    assert channel.nacks == [{"delivery_tag": 3, "requeue": False}]


def test_dns_invalid_document_is_acked_without_retry(monkeypatch):
    value = actor(PublishResult.success("unused", 1), monkeypatch)

    def reject(body):
        del body
        raise DocumentBoundaryError("wrong_type", "bad domain")

    monkeypatch.setattr("bbp.dns_resolver.decode_domain", reject)
    channel = FakeChannel()

    value._handle_domain(
        channel,
        SimpleNamespace(delivery_tag=4, redelivered=False),
        None,
        b"{}",
    )

    assert channel.acks == [{"delivery_tag": 4}]
    assert channel.nacks == []


def test_dns_cycle_rejection_is_terminal_and_not_published(monkeypatch):
    provenance = {
        "star_server": {
            "trace_id": "trace-1",
            "root_id": "root-1",
            "parent_id": "parent-1",
            "actor": "seed",
            "actor_path": ["seed", "dns-resolver"],
            "depth": 1,
            "hop": 1,
            "max_depth": 8,
            "max_hops": 32,
            "run_id": "run-1",
            "scan_id": "run-1",
        }
    }
    value = actor(PublishResult.success("unused", 1), monkeypatch)
    monkeypatch.setattr(
        "bbp.dns_resolver.decode_domain", lambda body: FakeDomain(provenance)
    )
    channel = FakeChannel()

    value._handle_domain(
        channel,
        SimpleNamespace(delivery_tag=5, redelivered=False),
        None,
        b"{}",
    )

    assert channel.acks == []
    assert channel.nacks == [{"delivery_tag": 5, "requeue": False}]
    assert value.publisher.required == []
    assert value.publisher.events[-1].event["eventType"] == "dns-rejected"
