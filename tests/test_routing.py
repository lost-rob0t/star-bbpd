from typing import Any, Dict, List, Optional, Tuple

from bbp.publisher_pool import PublishDocument, PublishRelation
from bbp.tool_actor import ToolActor


class FakePublisher:
    def __init__(self):
        self.messages = []

    def tell(self, message):
        self.messages.append(message)


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
    actor.routing = {
        "document_ingest_key": "documents.ingest.{dtype}",
        "event_key": "event.{eventType}",
    }
    return actor


def test_documents_use_durable_ingest_route():
    actor = make_actor()
    actor._emit_document(FakeDocument())

    message = actor.publisher.messages.pop()
    assert isinstance(message, PublishDocument)
    assert message.routing_key == "documents.ingest.domain"


def test_relations_use_durable_ingest_route():
    actor = make_actor()
    actor._emit_relation(FakeRelation())

    message = actor.publisher.messages.pop()
    assert isinstance(message, PublishRelation)
    assert message.routing_key == "documents.ingest.relation"
