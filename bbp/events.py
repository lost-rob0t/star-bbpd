#!/usr/bin/env python3

import time
from typing import Dict, Any

from ulid import ULID


def create_event(
    actor_name: str, event_type: str, details: str, source_id: str
) -> Dict[str, Any]:
    """
    Create an actor event matching gserver format.

    Args:
        actor_name: Name of the actor emitting the event
        event_type: Type of event (scan-start, scan-finished, scan-error, etc.)
        details: Freeform details string
        source_id: ID of the source document (usually target._id)

    Returns:
        Event dict with _id, timestamp, actorName, eventType, details, sourceId

    Event Types:
        - service-start: Service starting up
        - service-ready: Service ready to accept targets
        - service-shutdown: Service shutting down
        - scan-start: Tool scan starting
        - scan-finished: Tool scan completed successfully
        - scan-error: Tool scan failed or timed out
        - publish-error: Failed to publish document/relation
        - parse-error: Failed to parse target JSON
    """
    return {
        "_id": str(ULID()),
        "timestamp": int(time.time()),
        "actorName": actor_name,
        "eventType": event_type,
        "details": details,
        "sourceId": source_id,
    }
