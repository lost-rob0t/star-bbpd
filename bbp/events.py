#!/usr/bin/env python3

import time
from typing import Any, Dict

import ulid


def create_event(
    actor_name: str, event_type: str, details: str, source_id: str
) -> Dict[str, Any]:
    """
    Create an actor event matching the current star-server payload.

    Lifecycle and diagnostic events are best-effort; required document and
    relation settlement is handled separately by the publisher result protocol.
    """
    return {
        "_id": str(ulid.new()),
        "timestamp": int(time.time()),
        "actorName": actor_name,
        "eventType": event_type,
        "details": details,
        "sourceId": source_id,
    }
