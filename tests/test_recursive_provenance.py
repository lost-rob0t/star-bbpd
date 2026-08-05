from __future__ import annotations

from datetime import datetime

import pytest

from bbp.determinism import RecursionRejected, canonical_document_hash, validate_recursion
from bbp.document_boundary import (
    build_domain,
    build_target,
    build_url,
    serialize_document,
    with_domain_addresses,
)

NOW = "2026-08-05T12:00:00Z"


def make_target(*, actor: str = "subfinder", extensions=None):
    return build_target(
        actor,
        "example.com",
        "scan-a",
        extensions=extensions or {"example.test": {"keep": True}},
        now=NOW,
    )


def star(document):
    return document.extensions["star_server"]


def test_new_root_trace_is_created_deterministically():
    first = build_domain("api.example.com", make_target(), "subfinder")
    second = build_domain("api.example.com", make_target(), "subfinder")

    assert star(first)["trace_id"] == star(second)["trace_id"]
    assert star(first)["root_id"] == make_target().id
    assert star(first)["parent_id"] == make_target().id


def test_inherited_trace_root_parent_and_actor_path():
    extensions = {
        "example.test": {"keep": True},
        "star_server": {
            "trace_id": "trace-original",
            "root_id": "root-original",
            "parent_id": "parent-old",
            "actor": "seed",
            "actor_path": ["seed"],
            "depth": 1,
            "hop": 2,
            "max_depth": 8,
            "max_hops": 32,
            "run_id": "run-original",
            "scan_id": "run-original",
            "custom": "preserve",
        },
    }
    target = make_target(extensions=extensions)
    document = build_domain("api.example.com", target, "subfinder")
    value = star(document)

    assert value["trace_id"] == "trace-original"
    assert value["root_id"] == "root-original"
    assert value["parent_id"] == target.id
    assert value["actor"] == "subfinder"
    assert value["actor_path"] == ["seed", "subfinder"]
    assert value["depth"] == 2
    assert value["hop"] == 3
    assert value["run_id"] == value["scan_id"] == "run-original"
    assert value["custom"] == "preserve"
    assert document.extensions["example.test"] == {"keep": True}


def test_hashes_and_timestamp_are_stable_and_well_formed():
    document = build_domain("api.example.com", make_target(), "subfinder")
    value = star(document)

    assert value["input_hash"].startswith("sha256:")
    assert value["output_hash"].startswith("sha256:")
    assert len(value["input_hash"].split(":", 1)[1]) == 64
    assert len(value["output_hash"].split(":", 1)[1]) == 64
    assert canonical_document_hash(document.to_dict()) == value["output_hash"]
    assert datetime.fromisoformat(value["emitted_at"].replace("Z", "+00:00"))
    assert value["emitted_at"] == NOW


def test_subfinder_to_dns_provenance_chain_advances_parent_and_path():
    target = make_target()
    domain = build_domain("api.example.com", target, "subfinder")
    updated = with_domain_addresses(domain, ["2001:0db8::1", "192.0.2.1"])

    assert updated.id == domain.id
    assert star(updated)["trace_id"] == star(domain)["trace_id"]
    assert star(updated)["root_id"] == star(domain)["root_id"]
    assert star(updated)["parent_id"] == domain.id
    assert star(updated)["actor_path"] == ["subfinder", "dns-resolver"]
    assert star(updated)["depth"] == star(domain)["depth"] + 1
    assert star(updated)["hop"] == star(domain)["hop"] + 1
    assert star(updated)["run_id"] == star(domain)["run_id"]


def test_second_recursive_actor_chain_preserves_trace_and_run():
    first = build_url("https://example.com/", make_target(actor="httpx"), "httpx")
    next_target = build_target(
        "katana",
        first.data["url"],
        first.dataset,
        extensions=first.extensions,
        now=first.to_dict()["date_updated"],
    )
    second = build_url("https://example.com/a", next_target, "katana")

    assert star(second)["trace_id"] == star(first)["trace_id"]
    assert star(second)["root_id"] == star(first)["root_id"]
    assert star(second)["run_id"] == star(first)["run_id"]
    assert star(second)["actor_path"] == ["httpx", "katana"]
    assert star(second)["parent_id"] == next_target.id


def state(*, depth=0, hop=0, max_depth=8, max_hops=32, path=()):
    return {
        "_id": "source-1",
        "date_updated": NOW,
        "extensions": {
            "star_server": {
                "trace_id": "trace-1",
                "root_id": "root-1",
                "parent_id": None,
                "actor": "seed",
                "actor_path": list(path),
                "depth": depth,
                "hop": hop,
                "max_depth": max_depth,
                "max_hops": max_hops,
                "run_id": "run-1",
                "scan_id": "run-1",
            }
        },
    }


def test_depth_below_limit_succeeds():
    value = validate_recursion(state(depth=7, max_depth=8), "dns-resolver")
    assert value.depth == 8


def test_depth_at_limit_is_permanent_rejection():
    with pytest.raises(RecursionRejected) as exc:
        validate_recursion(state(depth=8, max_depth=8), "dns-resolver")
    assert exc.value.category == "depth_budget_exceeded"


def test_hop_below_limit_succeeds():
    value = validate_recursion(state(hop=31, max_hops=32), "dns-resolver")
    assert value.hop == 32


def test_hop_at_limit_is_permanent_rejection():
    with pytest.raises(RecursionRejected) as exc:
        validate_recursion(state(hop=32, max_hops=32), "dns-resolver")
    assert exc.value.category == "hop_budget_exceeded"


def test_actor_path_cycle_is_rejected():
    with pytest.raises(RecursionRejected) as exc:
        validate_recursion(state(path=("subfinder", "dns-resolver")), "dns-resolver")
    assert exc.value.category == "actor_cycle"


@pytest.mark.parametrize(
    "field,value",
    [
        ("depth", "1"),
        ("hop", True),
        ("max_depth", 0),
        ("max_hops", -1),
        ("actor_path", "subfinder"),
        ("trace_id", ""),
        ("root_id", None),
    ],
)
def test_malformed_provenance_is_rejected(field, value):
    document = state()
    document["extensions"]["star_server"][field] = value
    with pytest.raises(RecursionRejected) as exc:
        validate_recursion(document, "dns-resolver")
    assert exc.value.category == "malformed_provenance"


def test_replay_serialization_preserves_provenance_bytes():
    first = build_domain("api.example.com", make_target(), "subfinder")
    second = build_domain("api.example.com", make_target(), "subfinder")
    assert serialize_document(first) == serialize_document(second)
