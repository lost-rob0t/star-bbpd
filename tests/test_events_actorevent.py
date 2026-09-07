from __future__ import annotations

import time

from bbp.events import create_event


def test_event_conforms_to_starintel_server_event_contract():
    before = int(time.time())
    event = create_event("subfinder", "scan-start", "run_id=r; details", "target:1")
    after = int(time.time())

    assert isinstance(event["_id"], str) and event["_id"]
    assert before <= event["timestamp"] <= after
    assert event["dtype"] == "actorevent"
    assert event["actorName"] == "subfinder"
    assert event["eventType"] == "scan-start"
    assert event["details"] == "run_id=r; details"
    assert event["sourceId"] == "target:1"
    assert isinstance(event["generation"], int) and event["generation"] >= 0


def test_event_validates_against_server_side_rules():
    # Mirrors starintel-server source/actor-systems/event-actor.lisp
    # validation: non-empty _id, positive timestamp, dtype == actorevent,
    # actorName or component present, eventType present, generation >= 0.
    event = create_event("dns-resolver", "parse-error", "category=x", "")
    assert event["dtype"] == "actorevent"
    assert event["actorName"] or event.get("component")
    assert event["eventType"]
    assert event["generation"] >= 0
    assert event["timestamp"] > 0
