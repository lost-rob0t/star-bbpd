from __future__ import annotations

import io
import subprocess
from dataclasses import dataclass

from bbp.consumer import AckMessage, NackMessage
from bbp.settlement import FailureKind, PublishResult
from bbp.tool_actor import ScanTarget, ToolActor


@dataclass
class FakeDoc:
    dtype: str
    id: str
    source: str = "source-1"
    target: str = "target-1"
    predicate: str = "related-to"

    def to_json(self):
        return "{}"


class FakeTarget:
    target = "example.com"
    id = "target-1"


class FakePublisher:
    def __init__(self, results):
        self.results = list(results)
        self.required = []
        self.events = []

    def publish_required(self, message, timeout=None):
        self.required.append((message, timeout))
        return self.results.pop(0)

    def tell(self, message):
        self.events.append(message)


class FakeConsumer:
    def __init__(self):
        self.messages = []

    def tell(self, message):
        self.messages.append(message)


class FakeProcess:
    def __init__(self, stdout_text="", exit_code=0, timeout=False):
        self.stdout = io.StringIO(stdout_text)
        self.stderr = io.StringIO("timeout detail" if timeout else "")
        self.returncode = None
        self.exit_code = exit_code
        self.timeout = timeout
        self.terminated = False
        self.killed = False

    def wait(self, timeout=None):
        if self.timeout and not self.terminated and not self.killed:
            raise subprocess.TimeoutExpired("tool", timeout)
        if self.returncode is None:
            self.returncode = -9 if self.killed else (-15 if self.terminated else self.exit_code)
        return self.returncode

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True


class DummyActor(ToolActor):
    def _build_command(self, target):
        del target
        return ["dummy"]

    def _parse_line(self, line, target, context):
        del target, context
        value = line.strip()
        if value == "doc":
            return FakeDoc("domain", "domain-1"), None
        if value == "both":
            return (
                FakeDoc("domain", "domain-1"),
                FakeDoc("relation", "relation-1"),
            )
        return None


def actor(publisher):
    value = DummyActor("dummy", publisher, {"timeout_sec": 2})
    value.routing = {
        "document_ingest_key": "documents.ingest.{dtype}",
        "event_key": "event.{eventType}",
    }
    return value


def success(key):
    return PublishResult.success(key, 1)


def failure(key, kind):
    return PublishResult.failure(key, 1, kind, "failed")


def test_ack_happens_only_after_all_required_publishes(monkeypatch):
    process = FakeProcess("both\n")
    monkeypatch.setattr("bbp.tool_actor.subprocess.Popen", lambda *a, **k: process)
    publisher = FakePublisher(
        [
            success("documents.ingest.domain"),
            success("documents.ingest.relation"),
        ]
    )
    consumer = FakeConsumer()

    result = actor(publisher)._execute_scan(
        ScanTarget(FakeTarget(), 7, consumer, redelivered=False)
    )

    assert result.ok is True
    assert len(publisher.required) == 2
    assert consumer.messages == [AckMessage(7)]


def test_forced_publish_failure_never_acks(monkeypatch):
    process = FakeProcess("doc\n")
    monkeypatch.setattr("bbp.tool_actor.subprocess.Popen", lambda *a, **k: process)
    publisher = FakePublisher(
        [failure("documents.ingest.domain", FailureKind.TRANSIENT)]
    )
    consumer = FakeConsumer()

    result = actor(publisher)._execute_scan(
        ScanTarget(FakeTarget(), 8, consumer, redelivered=False)
    )

    assert result.ok is False
    assert consumer.messages == [NackMessage(8, True)]
    assert process.terminated is True


def test_partial_batch_failure_nacks(monkeypatch):
    process = FakeProcess("both\n")
    monkeypatch.setattr("bbp.tool_actor.subprocess.Popen", lambda *a, **k: process)
    publisher = FakePublisher(
        [
            success("documents.ingest.domain"),
            failure("documents.ingest.relation", FailureKind.TRANSIENT),
        ]
    )
    consumer = FakeConsumer()

    result = actor(publisher)._execute_scan(
        ScanTarget(FakeTarget(), 9, consumer, redelivered=False)
    )

    assert result.document_count == 1
    assert result.relation_count == 0
    assert consumer.messages == [NackMessage(9, True)]


def test_redelivered_transient_failure_is_not_requeued(monkeypatch):
    process = FakeProcess("doc\n")
    monkeypatch.setattr("bbp.tool_actor.subprocess.Popen", lambda *a, **k: process)
    publisher = FakePublisher(
        [failure("documents.ingest.domain", FailureKind.TRANSIENT)]
    )
    consumer = FakeConsumer()

    actor(publisher)._execute_scan(
        ScanTarget(FakeTarget(), 10, consumer, redelivered=True)
    )

    assert consumer.messages == [NackMessage(10, False)]


def test_unroutable_permanent_failure_is_not_requeued(monkeypatch):
    process = FakeProcess("doc\n")
    monkeypatch.setattr("bbp.tool_actor.subprocess.Popen", lambda *a, **k: process)
    publisher = FakePublisher(
        [failure("documents.ingest.domain", FailureKind.PERMANENT)]
    )
    consumer = FakeConsumer()

    actor(publisher)._execute_scan(
        ScanTarget(FakeTarget(), 11, consumer, redelivered=False)
    )

    assert consumer.messages == [NackMessage(11, False)]


def test_timeout_nacks_and_requeues_first_delivery(monkeypatch):
    process = FakeProcess("", timeout=True)
    monkeypatch.setattr("bbp.tool_actor.subprocess.Popen", lambda *a, **k: process)
    publisher = FakePublisher([])
    consumer = FakeConsumer()

    result = actor(publisher)._execute_scan(
        ScanTarget(FakeTarget(), 12, consumer, redelivered=False)
    )

    assert result.failure_kind is FailureKind.TRANSIENT
    assert consumer.messages == [NackMessage(12, True)]
    assert process.terminated is True


class FailingEventPublisher(FakePublisher):
    def tell(self, message):
        raise RuntimeError("events unavailable")


def test_best_effort_event_failure_does_not_block_successful_ack(monkeypatch):
    process = FakeProcess("doc\n")
    monkeypatch.setattr("bbp.tool_actor.subprocess.Popen", lambda *a, **k: process)
    publisher = FailingEventPublisher([success("documents.ingest.domain")])
    consumer = FakeConsumer()

    result = actor(publisher)._execute_scan(
        ScanTarget(FakeTarget(), 13, consumer, redelivered=False)
    )

    assert result.ok is True
    assert consumer.messages == [AckMessage(13)]
    assert publisher.required[0][0].run_id == result.run_id
