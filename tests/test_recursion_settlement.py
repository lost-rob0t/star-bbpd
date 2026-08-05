from __future__ import annotations

from dataclasses import dataclass

import pytest

from bbp.consumer import NackMessage
from bbp.settlement import FailureKind
from bbp.tool_actor import ScanTarget, ToolActor

NOW = "2026-08-05T12:00:00Z"


@dataclass
class RecursiveTarget:
    path: tuple[str, ...] = ()
    depth: int = 0
    hop: int = 0
    max_depth: int = 8
    max_hops: int = 32
    id: str = "target-1"
    target: str = "example.com"

    def to_dict(self):
        return {
            "_id": self.id,
            "date_added": NOW,
            "date_updated": NOW,
            "extensions": {
                "star_server": {
                    "trace_id": "trace-1",
                    "root_id": "root-1",
                    "parent_id": "parent-1",
                    "actor": "seed",
                    "actor_path": list(self.path),
                    "depth": self.depth,
                    "hop": self.hop,
                    "max_depth": self.max_depth,
                    "max_hops": self.max_hops,
                    "run_id": "run-1",
                    "scan_id": "run-1",
                }
            },
        }


class FakePublisher:
    def __init__(self):
        self.events = []
        self.required = []

    def tell(self, message):
        self.events.append(message)

    def publish_required(self, message, timeout=None):
        self.required.append((message, timeout))
        raise AssertionError("rejected recursion must not publish required output")


class FakeConsumer:
    def __init__(self):
        self.messages = []

    def tell(self, message):
        self.messages.append(message)


class RejectBeforeRunActor(ToolActor):
    def _build_command(self, target):
        raise AssertionError("rejected recursion must not start a subprocess")

    def _parse_line(self, line, target, context):
        raise AssertionError("rejected recursion must not parse output")


def actor(name="dns-resolver"):
    publisher = FakePublisher()
    value = RejectBeforeRunActor(name, publisher, {"timeout_sec": 1})
    value.routing = {
        "document_ingest_key": "documents.ingest.{dtype}",
        "event_key": "event.{eventType}",
    }
    return value, publisher


@pytest.mark.parametrize(
    "target",
    [
        RecursiveTarget(depth=8, max_depth=8),
        RecursiveTarget(hop=32, max_hops=32),
        RecursiveTarget(path=("seed", "dns-resolver")),
    ],
)
def test_permanent_recursion_rejection_is_terminal_and_not_requeued(target):
    value, publisher = actor()
    consumer = FakeConsumer()

    result = value._execute_scan(ScanTarget(target, 17, consumer, redelivered=False))

    assert result.ok is False
    assert result.failure_kind is FailureKind.PERMANENT
    assert result.decision is not None
    assert result.decision.requeue is False
    assert consumer.messages == [NackMessage(17, False)]
    assert publisher.required == []
    assert publisher.events[-1].event["eventType"] == "scan-rejected"


def test_malformed_provenance_is_terminal_not_a_false_ack():
    target = RecursiveTarget()
    value = target.to_dict()
    value["extensions"]["star_server"]["depth"] = "1"
    target.to_dict = lambda: value
    tool, publisher = actor()
    consumer = FakeConsumer()

    result = tool._execute_scan(ScanTarget(target, 18, consumer, redelivered=True))

    assert result.ok is False
    assert result.failure_kind is FailureKind.PERMANENT
    assert consumer.messages == [NackMessage(18, False)]
    assert publisher.events[-1].event["eventType"] == "scan-rejected"
