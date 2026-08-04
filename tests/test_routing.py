from typing import Any, Dict, List, Optional, Tuple

from bbp.publisher_pool import PublishDocument, PublishRelation
from bbp.settlement import PublishResult
from bbp.tool_actor import ToolActor


class FakePublisher:
    def __init__(self):
        self.messages = []

    def tell(self, message):
        self.messages.append(message)

    def publish_required(self, message, timeout=None):
        self.messages.append(message)
        return PublishResult.success(message.routing_key, 1)


class FakeToolActor(ToolActor):
    def _build_command(self, target: Any) -> List[str]:
        return []

    def _parse_line(
        self, line: str, target: Any, context: Dict[str, Any]
    ) -> Optional[Tuple[Any, Any]]:
        return None


class FakeDocument:
    dtype = "domain"
    id = "domain:test"


class FakeRelation:
    dtype = "relation"
    source = "source:test"
    target = "target:test"
    predicate = "related-to"


def make_actor():
    actor = object.__new__(FakeToolActor)
    actor.actor_name = "test-tool"
    actor.publisher = FakePublisher()
    actor.publish_timeout = 30
    actor.routing = {
        "document_ingest_key": "documents.ingest.{dtype}",
        "event_key": "event.{eventType}",
    }
    return actor


def test_documents_use_confirmed_durable_ingest_route():
    actor = make_actor()
    result = actor._emit_document(FakeDocument(), "run-1")

    message = actor.publisher.messages.pop()
    assert result.ok is True
    assert isinstance(message, PublishDocument)
    assert message.routing_key == "documents.ingest.domain"
    assert message.run_id == "run-1"


def test_relations_use_confirmed_durable_ingest_route():
    actor = make_actor()
    result = actor._emit_relation(FakeRelation(), "run-2")

    message = actor.publisher.messages.pop()
    assert result.ok is True
    assert isinstance(message, PublishRelation)
    assert message.routing_key == "documents.ingest.relation"
    assert message.run_id == "run-2"
