from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from bbp.config import Config
from bbp.document_boundary import decode_document
from bbp.manifests import (
    ACTOR_MANIFESTS,
    actor_manifest_document,
    actor_resource_uri,
    enabled_actor_manifest_documents,
    publish_actor_manifests,
)
from bbp.settlement import FailureKind, PublishResult, RequiredPublishError
from bbp.tools import TOOL_CLASSES

ROOT = Path(__file__).resolve().parents[1]
NOW = "2026-09-16T21:25:00Z"


class FakePublisher:
    def __init__(self, result: PublishResult) -> None:
        self.result = result
        self.calls = []

    def publish_required(self, message, timeout):
        self.calls.append((message, timeout))
        return self.result


def load_example() -> Config:
    return Config.load(str(ROOT / "config.example.toml"))


def test_manifest_registry_covers_public_tool_actor_boundaries():
    assert set(TOOL_CLASSES) == set(ACTOR_MANIFESTS) - {"dns-resolver"}
    assert {"bbot", "nuclei", "http-proxy", "screenshot"} <= set(TOOL_CLASSES)


def test_enabled_manifests_use_current_wire_and_hierarchical_star_identity():
    config = load_example()
    documents = {
        document.to_dict()["data"]["actor"]: document.to_dict()
        for document in enabled_actor_manifest_documents(config, now=NOW)
    }

    expected = set(config.enabled_actors()) | {"dns-resolver"}
    assert set(documents) == expected
    for actor_id, document in documents.items():
        assert document["schema_version"] == "0.9.0"
        assert document["dtype"] == "actor-manifest"
        assert document["dataset"] == "starintel-system"
        assert set(document["data"]) == {
            "actor",
            "consumer_path",
            "target_options",
            "manifest_type",
            "name",
            "generated_at",
            "schema_versions",
        }
        contract = document["extensions"]["starintel.actor_manifest.v1"]
        assert contract["resourceUri"] == f"star://bbp.starintel.actor/actor/{actor_id}"
        assert contract["resource_kind"] == "actor"
        assert contract["semantic"]["digest"].startswith("sha256:")
        assert document["data"]["consumer_path"] not in contract["resourceUri"]
        assert contract["deployment"]["transport"] == "rabbitmq"
        assert contract["schema_release"] == "0.9.1"
        assert contract["schema_revision"] == "0.9.0+fields.20260909.1"
        decoded = decode_document(json.dumps(document), expected_dtype="actor-manifest")
        assert decoded.id == document["_id"]


def test_opt_in_extended_actor_manifest_uses_network_capture_profile_contract():
    config = load_example()
    for actor_id in ("bbot", "nuclei", "http-proxy", "screenshot"):
        document = actor_manifest_document(config, actor_id, now=NOW).to_dict()
        contract = document["extensions"]["starintel.actor_manifest.v1"]
        assert contract["resourceUri"] == f"star://bbp.starintel.actor/actor/{actor_id}"
        if actor_id == "http-proxy":
            assert "http-transaction" in contract["output_dtypes"]
        if actor_id == "screenshot":
            assert "web-capture" in contract["output_dtypes"]


def test_transport_queue_changes_do_not_change_actor_identity_or_semantic_digest():
    original = load_example()
    changed_data = deepcopy(original.data)
    changed_data["actors"]["subfinder"]["queue"] = "some.other.transport.queue"
    changed = Config(changed_data)

    before = actor_manifest_document(original, "subfinder", now=NOW).to_dict()
    after = actor_manifest_document(changed, "subfinder", now=NOW).to_dict()
    before_contract = before["extensions"]["starintel.actor_manifest.v1"]
    after_contract = after["extensions"]["starintel.actor_manifest.v1"]

    assert before_contract["resourceUri"] == after_contract["resourceUri"]
    assert before_contract["semantic"]["digest"] == after_contract["semantic"]["digest"]
    assert before_contract["deployment"]["consumer_path"] != after_contract["deployment"]["consumer_path"]


def test_resource_uri_rejects_legacy_transport_identity_shapes():
    assert actor_resource_uri("BBP.STARINTEL.ACTOR", "nmap") == "star://bbp.starintel.actor/actor/nmap"
    for authority in ("user@bbp", "bbp:5672", "bbp/path", "bbp?query"):
        with pytest.raises(ValueError):
            actor_resource_uri(authority, "nmap")


def test_manifest_publish_uses_ingest_route_and_required_settlement():
    config = load_example()
    document = actor_manifest_document(config, "httpx", now=NOW)
    publisher = FakePublisher(PublishResult.success("documents.ingest.actor-manifest", 1))

    count = publish_actor_manifests(config, publisher, documents=(document,))

    assert count == 1
    message, timeout = publisher.calls[0]
    assert message.routing_key == "documents.ingest.actor-manifest"
    assert message.doc.dtype == "actor-manifest"
    assert timeout == 30.0


def test_manifest_publish_failure_fails_closed_before_actor_readiness():
    config = load_example()
    document = actor_manifest_document(config, "katana", now=NOW)
    publisher = FakePublisher(
        PublishResult.failure(
            "documents.ingest.actor-manifest",
            1,
            FailureKind.PERMANENT,
            "unroutable",
        )
    )

    with pytest.raises(RequiredPublishError, match="unroutable"):
        publish_actor_manifests(config, publisher, documents=(document,))


def test_manifest_publication_can_be_explicitly_disabled_for_standalone_mode():
    config = load_example()
    data = deepcopy(config.data)
    data["service"]["publish_actor_manifests"] = False
    standalone = Config(data)
    publisher = FakePublisher(PublishResult.success("unused", 1))

    assert publish_actor_manifests(standalone, publisher) == 0
    assert publisher.calls == []


def test_manifest_emission_requires_a_configured_logical_authority():
    config = load_example()
    data = deepcopy(config.data)
    data["service"].pop("star_authority")
    missing = Config(data)

    with pytest.raises(ValueError, match="service.star_authority is required"):
        enabled_actor_manifest_documents(missing, now=NOW)
