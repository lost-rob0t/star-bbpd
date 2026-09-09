from __future__ import annotations

from bbp.actor_manifests import actor_manifest_document, build_actor_manifests
from bbp.config import Config


def config() -> Config:
    return Config(
        {
            "service": {"name": "bbp-test"},
            "documents": {"host": "127.0.0.1", "port": 5672, "exchange": "documents"},
            "events": {"exchange": "events"},
            "routing": {
                "target_key": "actors.{actor}.new.target",
                "document_ingest_key": "documents.ingest.{dtype}",
                "document_update_key": "documents.updated.{dtype}",
                "document_new_key": "documents.new.{dtype}",
                "event_key": "event.{eventType}",
            },
            "actors": {
                "subfinder": {"enabled": True, "queue": "bbp.subfinder.targets", "prefetch": 1},
                "nmap": {"enabled": False, "queue": "bbp.nmap.targets", "prefetch": 1},
            },
            "tools": {
                "subfinder": {"timeout_sec": 30, "publish_timeout_sec": 10},
                "nmap": {"timeout_sec": 30, "publish_timeout_sec": 10},
            },
        }
    )


def test_manifests_include_only_enabled_remote_actors() -> None:
    manifests = build_actor_manifests(config())
    assert [manifest["id"] for manifest in manifests] == ["subfinder"]
    manifest = manifests[0]
    assert manifest["schema"] == "starintel-actor-deployment-manifest-v1"
    assert manifest["service"] == {
        "id": "bbp-test",
        "kind": "star-bbpd",
        "language": "python",
    }
    assert manifest["runtime"]["location"] == "remote"
    assert manifest["runtime"]["transport"] == "rabbitmq"
    assert manifest["runtime"]["routing_key"] == "actors.subfinder.new.target"
    assert manifest["dispatch"] == {"dtype": "target", "actor": "subfinder"}


def test_manifest_document_has_stable_list_envelope() -> None:
    document = actor_manifest_document(config())
    assert document["status"] == "ok"
    assert document["data"]["schema"] == "starintel-actor-deployment-manifest-list-v1"
    assert document["data"]["service"] == "bbp-test"
    assert len(document["data"]["actors"]) == 1
