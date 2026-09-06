"""Canonical STAR URI identity for BBPD actors.

Implements the approved StarIntel identity model from STAR-RESEARCH-041 /
STAR-LANG-004:

    star://<authority>/<resource-kind>/<resource-path>

Canonicalization rules (protocol v1):

- scheme is canonical lowercase ``star``;
- authority is a lowercase RFC 3986 reg-name;
- userinfo, transport ports, dot segments, query, and fragment are forbidden;
- path segments use RFC 3986 pchar rules with uppercase percent-encoding;
- percent-encoded unreserved characters normalize to unreserved form;
- resource kind must be a reviewed STAR resource class.

The legacy actor-tuple form ``star://domain:address:actor-name`` is accepted
only through :func:`parse_legacy_star_uri` as bounded compatibility input.
It is never emitted by BBPD.

TODO(starlang-migration):
This module is the BBPD-side projection of the shared STAR URI contract.
It remains until the equivalent StarLang/shared URI library is consumable
from the Python runtime; replace it, do not port it further.
See auto-research STAR-RESEARCH-041 and STAR-LANG-004.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Iterable

__all__ = [
    "StarUriError",
    "MalformedStarUri",
    "NoncanonicalStarUri",
    "StarUri",
    "RESOURCE_KINDS",
    "parse_star_uri",
    "actor_service_uri",
    "actor_path_segment",
    "parse_legacy_star_uri",
    "is_canonical_star_uri",
]

RESOURCE_KINDS = frozenset(
    {"document", "actor", "service", "target", "dataset", "relation", "job"}
)

_UNRESERVED = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
)
_PCT = re.compile(r"%([0-9A-Fa-f]{2})")
# RFC 3986 reg-name profile: unreserved / sub-delims, no userinfo, no port.
_AUTHORITY = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-._~]*[A-Za-z0-9])?$")
_PCHAR = re.compile(
    r"^(?:[A-Za-z0-9._~!$&'()*+,;=:@-]|%[0-9A-Fa-f]{2})*$"
)
_LEGACY_TUPLE = re.compile(r"^([A-Za-z0-9._-]+):([A-Za-z0-9._-]+):(.+)$")


class StarUriError(ValueError):
    """Base error for STAR URI handling."""


class MalformedStarUri(StarUriError):
    """The input is not a parseable star URI."""


class NoncanonicalStarUri(StarUriError):
    """The input parses but violates canonical v1 STAR identity rules."""


@dataclass(frozen=True, slots=True)
class StarUri:
    authority: str
    resource_kind: str
    resource_path: tuple[str, ...]

    @property
    def path_segments(self) -> tuple[str, ...]:
        return (self.resource_kind, *self.resource_path)

    def __str__(self) -> str:
        path = "/".join(self.path_segments)
        return f"star://{self.authority}/{path}"


def _normalize_percent(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        byte = int(match.group(1), 16)
        char = chr(byte)
        return char if char in _UNRESERVED else f"%{byte:02X}"

    return _PCT.sub(replace, value)


def _normalize_segment(segment: str, index: int) -> str:
    if not segment:
        raise MalformedStarUri(f"path segment {index} is empty")
    if not _PCHAR.match(segment):
        raise MalformedStarUri(
            f"path segment {index} contains characters outside RFC 3986 pchar"
        )
    if segment in {".", ".."}:
        raise NoncanonicalStarUri(
            f"dot segment {segment!r} is rejected in canonical STAR identity"
        )
    return _normalize_percent(segment)


@lru_cache(maxsize=256)
def parse_star_uri(value: str) -> StarUri:
    """Parse and canonicalize a RFC 3986 star URI, or raise StarUriError."""
    if not isinstance(value, str) or not value:
        raise MalformedStarUri("star URI must be a non-empty string")
    if not value.startswith("star://"):
        raise MalformedStarUri("star URI must start with 'star://'")
    rest = value[len("star://") :]
    if "/" not in rest:
        raise MalformedStarUri("star URI requires an authority and a path")
    authority, _, path = rest.partition("/")
    if not authority:
        raise MalformedStarUri("authority is required")
    if "@" in authority:
        raise NoncanonicalStarUri("userinfo is forbidden in canonical STAR identity")
    if ":" in authority:
        raise NoncanonicalStarUri(
            "transport ports and colon syntax are forbidden in canonical STAR identity"
        )
    if not _AUTHORITY.match(authority):
        raise MalformedStarUri(f"invalid authority: {authority!r}")
    if "?" in path or "#" in path:
        raise NoncanonicalStarUri(
            "query and fragment do not participate in canonical STAR identity"
        )
    if path.endswith("/"):
        raise NoncanonicalStarUri("trailing slash is not canonical")
    raw_segments = path.split("/")
    segments = tuple(
        _normalize_segment(segment, index)
        for index, segment in enumerate(raw_segments)
    )
    kind = segments[0]
    if kind not in RESOURCE_KINDS:
        raise NoncanonicalStarUri(
            f"resource kind {kind!r} is not a reviewed STAR resource class"
        )
    if authority != authority.lower():
        raise NoncanonicalStarUri("authority must be canonical lowercase")
    return StarUri(authority=authority, resource_kind=kind, resource_path=segments[1:])


def is_canonical_star_uri(value: str) -> bool:
    try:
        return isinstance(parse_star_uri(value), StarUri)
    except StarUriError:
        return False


def actor_path_segment(actor_name: str) -> str:
    """Map a BBPD actor name to its canonical actor path segment.

    BBPD actor names are constrained to ``[a-z0-9-]`` after logical-name
    normalization, so the mapping is the identity for valid names.
    """
    if not isinstance(actor_name, str) or not actor_name:
        raise MalformedStarUri("actor name must be a non-empty string")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", actor_name):
        raise MalformedStarUri(
            f"actor name {actor_name!r} is not a canonical actor path segment"
        )
    return actor_name


def actor_service_uri(authority: str, actor_name: str) -> str:
    """Build the canonical service URI for a BBPD actor.

    Example::

        actor_service_uri("bbpd.starintel.actor", "subfinder")
        => "star://bbpd.starintel.actor/actor/subfinder"
    """
    parsed_authority = parse_star_uri(f"star://{authority}/service/identity")
    segment = actor_path_segment(actor_name)
    return str(
        StarUri(
            authority=parsed_authority.authority,
            resource_kind="actor",
            resource_path=(segment,),
        )
    )


def parse_legacy_star_uri(value: str) -> StarUri:
    """Parse the legacy ``star://domain:address:actor-name`` tuple.

    Compatibility input only (STAR-RESEARCH-041): the caller must supply the
    canonical authority; the legacy tuple is never reused as new identity and
    BBPD never emits this form.
    """
    if not isinstance(value, str) or not value.startswith("star://"):
        raise MalformedStarUri("legacy star URI must start with 'star://'")
    rest = value[len("star://") :]
    match = _LEGACY_TUPLE.match(rest)
    if not match:
        raise MalformedStarUri(
            "legacy star URI must be star://domain:address:actor-name"
        )
    domain, _address, actor_name = match.groups()
    segment = actor_path_segment(actor_name.lower())
    return StarUri(
        authority=f"legacy.{domain.lower()}",
        resource_kind="actor",
        resource_path=(segment,),
    )


def canonical_authorities(values: Iterable[str]) -> tuple[str, ...]:
    """Validate an iterable of authority strings, preserving order."""
    authorities: list[str] = []
    for value in values:
        probe = parse_star_uri(f"star://{value}/service/identity")
        authorities.append(probe.authority)
    return tuple(authorities)
