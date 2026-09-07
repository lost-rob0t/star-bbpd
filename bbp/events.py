#!/usr/bin/env python3

import time
from typing import Any, Dict

import ulid


def create_event(
    actor_name: str, event_type: str, details: str, source_id: str
) -> Dict[str, Any]:
    """
    Create an actor event matching the current star-server payload.

    Events conform to the starintel-server event contract
    (starintel-server source/actor-systems/event-actor.lisp): ``dtype`` is
    ``actorevent``, ``generation`` is a non-negative integer, and either
    ``actorName`` or ``component`` is required (BBPD always sends
    ``actorName``).

    Lifecycle and diagnostic events are best-effort; required document and
    relation settlement is handled separately by the publisher result protocol.
    """
    return {
        "_id": str(ulid.new()),
        "timestamp": int(time.time()),
        "dtype": "actorevent",
        "actorName": actor_name,
        "eventType": event_type,
        "details": details,
        "sourceId": source_id,
        "generation": 0,
    }
