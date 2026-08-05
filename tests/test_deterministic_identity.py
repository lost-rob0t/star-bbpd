from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from bbp.determinism import (
    canonical_json_bytes,
    normalize_addresses,
    normalize_domain_name,
    normalize_ip_address,
    normalize_url,
    relation_document_id,
)
from bbp.document_boundary import (
    DocumentBoundaryError,
    build_domain,
    build_host,
    build_relation,
    build_service,
    build_target,
    build_url,
    serialize_document,
)

NOW = "2026-08-05T12:00:00Z"


def target(actor: str = "subfinder"):
    return build_target(actor, "Example.COM.", "scan-a", now=NOW)


def test_domain_case_and_dns_root_dot_are_equivalent():
    first = build_domain(" API.Example.COM. ", target(), "subfinder")
    second = build_domain("api.example.com", target(), "subfinder")

    assert first.id == second.id
    assert first.data == second.data
    assert first.data["domain"] == "api.example.com"


def test_domain_record_type_is_canonical_and_semantic():
    first = build_domain("example.com", target(), "subfinder", record_type="a")
    second = build_domain("example.com.", target(), "subfinder", record_type="A")
    different = build_domain("example.com", target(), "subfinder", record_type="AAAA")

    assert first.id == second.id
    assert first.data["record_type"] == "A"
    assert first.id != different.id


def test_domain_addresses_are_canonical_sorted_and_deduplicated():
    first = build_domain(
        "example.com",
        target(),
        "subfinder",
        resolved_addresses=["2001:0db8::1", "192.0.2.10", "2001:db8:0:0::1"],
    )
    second = build_domain(
        "example.com.",
        target(),
        "subfinder",
        resolved_addresses=["2001:db8::1", "192.0.2.10"],
    )

    assert first.id == second.id
    assert first.data == second.data
    assert first.data["resolved_addresses"] == ["192.0.2.10", "2001:db8::1"]
    assert normalize_addresses(first.data["resolved_addresses"]) == first.data["resolved_addresses"]


def test_empty_domain_is_rejected():
    with pytest.raises(DocumentBoundaryError):
        build_domain(" . ", target(), "subfinder")


def test_ipv4_and_ipv6_use_standard_library_canonical_forms():
    assert normalize_ip_address(" 192.0.2.1 ") == "192.0.2.1"
    assert normalize_ip_address("2001:0db8:0000:0000:0000:0000:0000:0001") == "2001:db8::1"
    assert normalize_ip_address("2001:db8::1") == "2001:db8::1"


def test_host_normalizes_hostname_services_and_os_for_identity():
    services_a = [
        build_service(443, " HTTPS ", " nginx   1.25 "),
        build_service(80, "HTTP", " Apache  2.4 "),
        build_service(443, "https", "nginx 1.25"),
    ]
    services_b = [
        build_service(80, "http", "Apache 2.4"),
        build_service(443, "https", "nginx 1.25"),
    ]
    first = build_host(
        " API.Example.COM. ",
        "2001:0db8:0:0::1",
        services_a,
        target("nmap"),
        "nmap",
        os_name=" Linux   6.x ",
    )
    second = build_host(
        "api.example.com",
        "2001:db8::1",
        services_b,
        target("nmap"),
        "nmap",
        os_name="Linux 6.x",
    )

    assert first.id == second.id
    assert first.data == second.data
    assert first.data["hostname"] == "api.example.com"
    assert first.data["ip"] == "2001:db8::1"
    assert first.data["ports"] == [
        {"port": 80, "name": "http", "version": "Apache 2.4"},
        {"port": 443, "name": "https", "version": "nginx 1.25"},
    ]


def test_url_scheme_host_default_port_and_empty_path_are_equivalent():
    first = build_url("HTTPS://Example.COM:443", target("httpx"), "httpx")
    second = build_url("https://example.com/", target("httpx"), "httpx")

    assert first.id == second.id
    assert first.data == second.data
    assert first.data["url"] == "https://example.com/"


def test_url_ipv6_literal_and_percent_encoding_are_canonical():
    first = build_url(
        "http://[2001:0db8:0:0::1]:80/%7euser?q=%41%2f#frag%6dent",
        target("httpx"),
        "httpx",
    )
    second = build_url(
        "http://[2001:db8::1]/~user?q=A%2F#fragment",
        target("httpx"),
        "httpx",
    )

    assert first.id == second.id
    assert first.data["url"] == "http://[2001:db8::1]/~user?q=A%2F#fragment"


def test_url_fragment_is_preserved_and_identity_relevant():
    first = build_url("https://example.com/a#one", target("httpx"), "httpx")
    second = build_url("https://example.com/a#two", target("httpx"), "httpx")

    assert first.data["url"].endswith("#one")
    assert first.id != second.id


def test_url_query_order_and_duplicates_are_not_rewritten():
    first = build_url("https://example.com/?a=1&a=2&b=3", target("httpx"), "httpx")
    reordered = build_url("https://example.com/?b=3&a=1&a=2", target("httpx"), "httpx")
    collapsed = build_url("https://example.com/?a=2&b=3", target("httpx"), "httpx")

    assert first.data["query"] == "a=1&a=2&b=3"
    assert first.id != reordered.id
    assert first.id != collapsed.id


def test_internationalized_hostname_uses_idna():
    first = build_url("https://bücher.example/", target("httpx"), "httpx")
    second = build_url("https://xn--bcher-kva.example/", target("httpx"), "httpx")

    assert first.id == second.id
    assert first.data["url"] == "https://xn--bcher-kva.example/"
    assert normalize_domain_name("BÜCHER.example.") == "xn--bcher-kva.example"


def test_relation_identity_matches_server_length_prefixed_contract():
    expected = relation_document_id("scan-a", "source-1", "related-to", "target-2")
    relation = build_relation(
        "source-1",
        "target-2",
        "related-to",
        "derived",
        target("httpx"),
        "httpx",
    )

    assert relation.id == expected
    assert relation.id.startswith("relation:")


def test_relation_identity_is_directed_and_sensitive_to_predicate_and_dataset():
    base = relation_document_id("scan-a", "source", "related-to", "target")
    assert base != relation_document_id("scan-a", "target", "related-to", "source")
    assert base != relation_document_id("scan-a", "source", "links-to", "target")
    assert base != relation_document_id("scan-b", "source", "related-to", "target")


def test_relation_derivation_context_is_not_part_of_server_identity():
    first = build_relation(
        "source",
        "target",
        "related-to",
        "first derivation",
        target("httpx"),
        "httpx",
    )
    second = build_relation(
        "source",
        "target",
        "related-to",
        "second derivation",
        target("httpx"),
        "httpx",
    )

    assert first.id == second.id


def test_retrying_identical_output_is_byte_equivalent():
    first = build_domain("API.Example.com.", target(), "subfinder")
    second = build_domain("api.example.com", target(), "subfinder")

    assert first.id == second.id
    assert serialize_document(first) == serialize_document(second)
    assert canonical_json_bytes(first.to_dict()) == canonical_json_bytes(second.to_dict())


def test_deterministic_probe_is_stable_across_process_hash_seeds():
    script = Path(__file__).with_name("determinism_probe.py")
    outputs = []
    for seed in ("1", "777"):
        env = dict(__import__("os").environ, PYTHONHASHSEED=seed)
        outputs.append(subprocess.check_output([sys.executable, str(script)], env=env))
    assert outputs[0] == outputs[1]


def test_url_normalizer_rejects_relative_urls():
    with pytest.raises(ValueError):
        normalize_url("/relative")
