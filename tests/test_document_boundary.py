from __future__ import annotations

import json
from pathlib import Path

import pytest

from bbp.document_boundary import (
    DocumentBoundaryError,
    SchemaRevisionMismatch,
    build_domain,
    build_host,
    build_relation,
    build_service,
    build_target,
    build_url,
    decode_target,
    load_schema_contract,
    serialize_document,
    verify_package_contract,
)

NOW = "2026-08-04T20:00:00Z"


def object_schema(properties, required=(), additional=False):
    value = {
        "type": "object",
        "properties": properties,
        "additionalProperties": additional,
    }
    if required:
        value["required"] = list(required)
    return value


def schema():
    string = {"type": "string"}
    source = object_schema({"kind": string, "name": string})
    service = object_schema(
        {"port": {"type": "integer"}, "name": string, "version": string}
    )
    types = {
        "target": object_schema(
            {
                "actor": string,
                "target": string,
                "delay": {"type": "integer"},
                "recurring": {"type": "boolean"},
                "options": {"type": "array", "items": {}},
            },
            required=("target",),
        ),
        "domain": object_schema(
            {
                "domain": string,
                "record_type": string,
                "record": string,
                "resolved_addresses": {"type": "array", "items": string},
            },
            required=("domain",),
        ),
        "host": object_schema(
            {
                "hostname": string,
                "ip": string,
                "ports": {"type": "array", "items": service},
                "os": string,
            }
        ),
        "url": object_schema(
            {"url": string, "path": string, "query": string, "content": string},
            required=("url",),
        ),
        "relation": object_schema(
            {
                "subject": string,
                "predicate": string,
                "object": string,
                "source": string,
                "target": string,
                "directed": {"type": "boolean"},
                "note": string,
            },
            required=("subject", "predicate", "object"),
        ),
    }
    common = {
        "_id": string,
        "_rev": string,
        "dataset": string,
        "dtype": {"enum": sorted(types)},
        "schema_version": {"const": "0.9.0"},
        "version": {"type": "integer", "minimum": 1},
        "date_added": {"type": "string", "format": "date-time"},
        "date_updated": {"type": "string", "format": "date-time"},
        "sources": {"type": "array", "items": source},
        "evidence": {"type": "array", "items": object_schema({}, additional=True)},
        "data": {"type": "object"},
        "extensions": {"type": "object", "additionalProperties": True},
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": common,
        "required": [
            "_id",
            "dataset",
            "dtype",
            "schema_version",
            "version",
            "date_added",
            "date_updated",
            "sources",
            "evidence",
            "data",
        ],
        "additionalProperties": False,
        "allOf": [
            {
                "if": {"properties": {"dtype": {"const": dtype}}},
                "then": {"properties": {"data": data_schema}},
            }
            for dtype, data_schema in types.items()
        ],
    }


@pytest.fixture()
def contract(tmp_path: Path):
    schemas = tmp_path / "schemas"
    schemas.mkdir()
    schema_path = schemas / "starintel-doc-v0.9.0.schema.json"
    manifest_path = schemas / "starintel-doc-v0.9.0.manifest.json"
    schema_path.write_text(json.dumps(schema()), encoding="utf-8")
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": "0.9.0",
                "release_version": "0.9.1",
                "schema_revision": "0.9.0+fields.20260909.1",
            }
        ),
        encoding="utf-8",
    )
    load_schema_contract.cache_clear()
    return load_schema_contract(schema_path, manifest_path)


def target_value(**overrides):
    value = {
        "_id": "target-1",
        "dataset": "scan-a",
        "dtype": "target",
        "schema_version": "0.9.0",
        "version": 1,
        "date_added": NOW,
        "date_updated": NOW,
        "sources": [{"kind": "manual", "name": "operator"}],
        "evidence": [],
        "data": {
            "actor": "subfinder",
            "target": "example.com",
            "delay": 0,
            "recurring": False,
            "options": [],
        },
        "extensions": {"example.test": {"keep": True}},
    }
    value.update(overrides)
    return value


def decode(value, contract):
    return decode_target(json.dumps(value), contract=contract)


def test_valid_09_target_decoding(contract):
    target = decode(target_value(), contract)
    assert target.id == "target-1"
    assert target.dtype == "target"


def test_unsupported_schema_version(contract):
    value = target_value(schema_version="0.8.0")
    with pytest.raises(DocumentBoundaryError, match="unsupported spec version") as exc:
        decode(value, contract)
    assert exc.value.category == "unsupported_spec_version"


def test_alias_dtype_rejected(contract):
    value = target_value(dtype="investigation_target")
    with pytest.raises(DocumentBoundaryError) as exc:
        decode(value, contract)
    assert exc.value.category == "invalid_enum"


def test_missing_required_target_field(contract):
    value = target_value()
    del value["data"]["actor"]
    with pytest.raises(DocumentBoundaryError) as exc:
        decode(value, contract)
    assert exc.value.category == "missing_required_field"


def test_wrong_field_type(contract):
    value = target_value()
    value["data"]["delay"] = "soon"
    with pytest.raises(DocumentBoundaryError) as exc:
        decode(value, contract)
    assert exc.value.category == "wrong_type"


def test_undeclared_field_rejected(contract):
    value = target_value()
    value["data"]["legacyActor"] = "subfinder"
    with pytest.raises(DocumentBoundaryError) as exc:
        decode(value, contract)
    assert exc.value.category == "undeclared_field"


def test_malformed_timestamp_rejected(contract):
    value = target_value(date_added="2026-08-04 20:00:00")
    with pytest.raises(DocumentBoundaryError) as exc:
        decode(value, contract)
    assert exc.value.category == "invalid_datetime"


def test_canonical_target_field_access(contract):
    target = decode(target_value(), contract)
    assert target.actor == "subfinder"
    assert target.target == "example.com"
    assert target.dataset == "scan-a"
    assert target.options == []


def test_target_document_construction(contract):
    document = build_target(
        "httpx",
        "example.com",
        "scan-a",
        sources=[{"kind": "manual", "name": "operator"}],
        contract=contract,
        now=NOW,
    )
    assert document.actor == "httpx"
    assert document.target == "example.com"
    assert document.to_dict()["schema_version"] == "0.9.0"


def test_domain_document_construction(contract):
    target = decode(target_value(), contract)
    document = build_domain(
        "api.example.com", target, "subfinder", contract=contract, now=NOW
    )
    value = document.to_dict()
    assert value["schema_version"] == "0.9.0"
    assert value["data"]["domain"] == "api.example.com"
    assert value["data"]["record"] == "api.example.com"


def test_host_and_service_document_construction(contract):
    target = decode(target_value(), contract)
    service = build_service(443, "https", "nginx 1.25")
    document = build_host(
        "api.example.com",
        "192.0.2.1",
        [service],
        target,
        "nmap",
        contract=contract,
        now=NOW,
    )
    assert document.to_dict()["data"]["ports"] == [
        {"port": 443, "name": "https", "version": "nginx 1.25"}
    ]


def test_url_document_construction(contract):
    target = decode(target_value(), contract)
    document = build_url(
        "https://example.com/a",
        target,
        "httpx",
        path="/a",
        contract=contract,
        now=NOW,
    )
    assert document.to_dict()["data"]["url"] == "https://example.com/a"


def test_relation_construction(contract):
    target = decode(target_value(), contract)
    relation = build_relation(
        "source-1",
        "target-2",
        "related-to",
        "httpx",
        target,
        "httpx",
        contract=contract,
        now=NOW,
    )
    data = relation.to_dict()["data"]
    assert data["subject"] == data["source"] == "source-1"
    assert data["object"] == data["target"] == "target-2"


def test_serialization_round_trip(contract):
    target = decode(target_value(), contract)
    document = build_domain(
        "api.example.com", target, "subfinder", contract=contract, now=NOW
    )
    decoded = json.loads(serialize_document(document))
    assert decoded == document.to_dict()


def test_extension_preservation(contract):
    target = decode(target_value(), contract)
    document = build_domain(
        "api.example.com", target, "subfinder", contract=contract, now=NOW
    )
    extensions = document.to_dict()["extensions"]
    assert extensions["example.test"] == {"keep": True}
    assert extensions["star_server"]["actor"] == "subfinder"
    assert extensions["star_server"]["parent_id"] == target.id
    assert {"kind": "tool", "name": "subfinder"} in document.to_dict()["sources"]


def test_schema_revision_mismatch(tmp_path: Path):
    schema_path = tmp_path / "starintel-doc-v0.9.0.schema.json"
    manifest_path = tmp_path / "starintel-doc-v0.9.0.manifest.json"
    schema_path.write_text(json.dumps(schema()), encoding="utf-8")
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": "0.9.0",
                "release_version": "0.9.1",
                "schema_revision": "wrong",
            }
        ),
        encoding="utf-8",
    )
    load_schema_contract.cache_clear()
    with pytest.raises(SchemaRevisionMismatch):
        load_schema_contract(schema_path, manifest_path)


def test_release_version_mismatch(tmp_path: Path):
    schema_path = tmp_path / "starintel-doc-v0.9.0.schema.json"
    manifest_path = tmp_path / "starintel-doc-v0.9.0.manifest.json"
    schema_path.write_text(json.dumps(schema()), encoding="utf-8")
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": "0.9.0",
                "release_version": "0.9.0",
                "schema_revision": "0.9.0+fields.20260909.1",
            }
        ),
        encoding="utf-8",
    )
    load_schema_contract.cache_clear()
    with pytest.raises(SchemaRevisionMismatch, match="release='0.9.0'"):
        load_schema_contract(schema_path, manifest_path)


def test_package_revision_mismatch():
    with pytest.raises(SchemaRevisionMismatch):
        verify_package_contract("0.8.2")


def test_package_revision_accepts_091():
    verify_package_contract("0.9.1")


def test_legacy_flat_target_is_not_adapted(contract):
    legacy = {
        "_id": "target-1",
        "dataset": "scan-a",
        "dtype": "target",
        "version": "0.8.0",
        "dateAdded": 1,
        "dateUpdated": 1,
        "sources": [],
        "actor": "subfinder",
        "target": "example.com",
    }
    with pytest.raises(DocumentBoundaryError) as exc:
        decode(legacy, contract)
    assert exc.value.category in {"unsupported_spec_version", "missing_required_field"}


@pytest.mark.skipif(
    not (
        Path.cwd()
        / "conformance-source"
        / "schemas"
        / "starintel-doc-v0.9.0.schema.json"
    ).is_file()
    and not __import__("os").environ.get("STARINTEL_CONFORMANCE_ROOT"),
    reason="pinned conformance source is not available",
)
def test_builders_against_pinned_schema():
    load_schema_contract.cache_clear()
    pinned = load_schema_contract()
    assert pinned.release_version == "0.9.1"
    dtype_names = {
        branch.get("if", {}).get("properties", {}).get("dtype", {}).get("const")
        for branch in pinned.schema.get("allOf", [])
    }
    assert {"observation", "operation"} <= dtype_names

    target = decode_target(json.dumps(target_value()), contract=pinned)
    domain = build_domain(
        "api.example.com", target, "subfinder", contract=pinned, now=NOW
    )
    service = build_service(443, "https", "nginx 1.25")
    host = build_host(
        "api.example.com",
        "192.0.2.1",
        [service],
        target,
        "nmap",
        contract=pinned,
        now=NOW,
    )
    url = build_url(
        "https://api.example.com", target, "httpx", contract=pinned, now=NOW
    )
    relation = build_relation(
        domain.id,
        url.id,
        "related-to",
        "test",
        target,
        "httpx",
        contract=pinned,
        now=NOW,
    )
    assert {domain.dtype, host.dtype, url.dtype, relation.dtype} == {
        "domain",
        "host",
        "url",
        "relation",
    }
