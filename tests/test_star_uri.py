from __future__ import annotations

import pytest

from bbp.star_uri import (
    MalformedStarUri,
    NoncanonicalStarUri,
    StarUri,
    actor_path_segment,
    actor_service_uri,
    is_canonical_star_uri,
    parse_legacy_star_uri,
    parse_star_uri,
)


class TestCanonicalParsing:
    def test_parses_single_segment_actor_uri(self):
        uri = parse_star_uri("star://bbpd.starintel.actor/actor/nmap")
        assert uri == StarUri("bbpd.starintel.actor", "actor", ("nmap",))

    def test_parses_multi_segment_actor_path(self):
        uri = parse_star_uri("star://gserver.starintel.actor/actor/quasar/user-hunt")
        assert uri.resource_path == ("quasar", "user-hunt")
        assert str(uri) == "star://gserver.starintel.actor/actor/quasar/user-hunt"

    def test_roundtrip_is_idempotent(self):
        value = "star://bbpd.starintel.actor/actor/subfinder"
        assert str(parse_star_uri(value)) == value

    def test_resource_kinds_are_enforced(self):
        for kind in ("document", "actor", "service", "target", "dataset", "relation", "job"):
            assert parse_star_uri(f"star://a.example/{kind}/x").resource_kind == kind

    def test_lowercased_percent_unreserved_normalizes(self):
        assert parse_star_uri("star://a.example/actor/%68ttpx").resource_path == ("httpx",)


class TestCanonicalizationRules:
    @pytest.mark.parametrize(
        "value",
        [
            "star://User.Example/actor/x",          # uppercase authority
            "star://user:secret@example/actor/x",   # userinfo
            "star://example:5672/actor/x",          # transport port
            "star://example/actor/../secret",       # dot segment
            "star://example/actor/x?trace=1",       # query
            "star://example/actor/x#frag",          # fragment
            "star://example/actor/x/",              # trailing slash
            "star://example//actor/x",              # empty segment
            "star://example/unknown-kind/x",        # unreviewed resource kind
        ],
    )
    def test_noncanonical_inputs_are_rejected(self, value):
        with pytest.raises((NoncanonicalStarUri, MalformedStarUri)):
            parse_star_uri(value)

    @pytest.mark.parametrize(
        "value",
        ["", "star:/example/actor/x", "example/actor/x", "star://example", "star:///actor/x"],
    )
    def test_malformed_inputs_are_rejected(self, value):
        with pytest.raises(MalformedStarUri):
            parse_star_uri(value)

    def test_uppercase_in_path_stays_resource_contract_defined(self):
        uri = parse_star_uri("star://a.example/actor/Node-1")
        assert uri.resource_path == ("Node-1",)


class TestLegacyCompatibilityInput:
    def test_legacy_tuple_parses_only_through_explicit_api(self):
        uri = parse_legacy_star_uri("star://bbp:localhost:nmap")
        assert uri.resource_kind == "actor"
        assert uri.resource_path == ("nmap",)

    def test_legacy_tuple_is_never_canonical(self):
        assert not is_canonical_star_uri("star://bbp:localhost:nmap")

    def test_legacy_parse_rejects_garbage(self):
        with pytest.raises(MalformedStarUri):
            parse_legacy_star_uri("star://just-one-segment")


class TestActorServiceUris:
    def test_builds_canonical_actor_uri(self):
        assert (
            actor_service_uri("bbpd.starintel.actor", "subfinder")
            == "star://bbpd.starintel.actor/actor/subfinder"
        )

    def test_rejects_invalid_actor_segment(self):
        with pytest.raises(MalformedStarUri):
            actor_path_segment("../etc")
        with pytest.raises(MalformedStarUri):
            actor_service_uri("bbpd.starintel.actor", "bad actor")

    def test_rejects_invalid_authority(self):
        with pytest.raises((MalformedStarUri, NoncanonicalStarUri)):
            actor_service_uri("bad:authority", "subfinder")
