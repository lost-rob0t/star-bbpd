from __future__ import annotations

from dataclasses import dataclass

from pika.exceptions import UnroutableError

from bbp.publisher_pool import PublishDocument, PublisherWorker
from bbp.settlement import FailureKind


@dataclass
class FakeDoc:
    dtype: str = "domain"
    id: str = "domain-1"

    def to_json(self):
        return '{"dtype":"domain"}'


class FakeChannel:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.is_open = True
        self.confirmed = False
        self.calls = []

    def confirm_delivery(self):
        self.confirmed = True

    def basic_publish(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def close(self):
        self.is_open = False


class FakeConnection:
    def __init__(self, channel):
        self._channel = channel
        self.is_open = True

    def channel(self):
        return self._channel

    def close(self):
        self.is_open = False


class FakeManager:
    def __init__(self, channels):
        self.channels = list(channels)

    def new_connection(self, connection_name=None):
        del connection_name
        return FakeConnection(self.channels.pop(0))


def worker(manager, retries=3):
    return PublisherWorker(
        manager,
        {
            "documents": {"exchange": "documents"},
            "events": {"exchange": "events"},
            "service": {
                "publisher_max_retries": retries,
                "publisher_retry_delays_sec": [0, 0, 0],
            },
        },
        0,
    )


def test_confirmed_publish_returns_success():
    channel = FakeChannel([True])
    result = worker(FakeManager([channel])).on_receive(
        PublishDocument(FakeDoc(), "documents.ingest.domain", "run-1")
    )

    assert result.ok is True
    assert result.attempts == 1
    assert channel.confirmed is True
    assert channel.calls[0]["mandatory"] is True


def test_transient_failure_reconnects_and_retries():
    first = FakeChannel([OSError("lost")])
    second = FakeChannel([True])
    result = worker(FakeManager([first, second])).on_receive(
        PublishDocument(FakeDoc(), "documents.ingest.domain", "run-1")
    )

    assert result.ok is True
    assert result.attempts == 2
    assert first.is_open is False
    assert second.confirmed is True


def test_unroutable_publish_is_permanent():
    channel = FakeChannel([UnroutableError([])])
    result = worker(FakeManager([channel])).on_receive(
        PublishDocument(FakeDoc(), "documents.ingest.missing", "run-1")
    )

    assert result.ok is False
    assert result.failure_kind is FailureKind.PERMANENT
    assert result.attempts == 1
    assert "unroutable" in result.error


def test_retry_exhaustion_is_transient():
    channels = [FakeChannel([OSError("lost")]) for _ in range(3)]
    result = worker(FakeManager(channels)).on_receive(
        PublishDocument(FakeDoc(), "documents.ingest.domain", "run-1")
    )

    assert result.ok is False
    assert result.failure_kind is FailureKind.TRANSIENT
    assert result.attempts == 3
