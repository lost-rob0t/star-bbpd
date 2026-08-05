from __future__ import annotations

import json
import re
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from ipaddress import ip_address
from typing import Any, Mapping, Sequence
from urllib.parse import SplitResult, urlsplit, urlunsplit

JsonObject = dict[str, Any]
DEFAULT_MAX_DEPTH = 8
DEFAULT_MAX_HOPS = 32
_UNRESERVED = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
)
_PERCENT = re.compile(r"%([0-9A-Fa-f]{2})")
_WORKER_SUFFIX = re.compile(r"-\d+$")
_PROVENANCE_FIELDS = frozenset(
    {
        "trace_id",
        "root_id",
        "parent_id",
        "actor",
        "actor_path",
        "depth",
        "hop",
        "max_depth",
        "max_hops",
        "derivation",
        "run_id",
        "scan_id",
        "input_hash",
        "output_hash",
        "emitted_at",
    }
)
CANONICAL_PREDICATES = frozenset(
    {
        "same-as", "duplicate-of", "aka", "alias-of", "username-of",
        "email-of", "phone-of", "account-of", "member-of", "employed-by",
        "contractor-for", "works-with", "manages", "reports-to", "owns",
        "owned-by", "controls", "controlled-by", "operates", "operated-by",
        "administers", "administered-by", "registered-to", "registrant-of",
        "whois-registrant-of", "whois-admin-of", "whois-tech-of", "located-at",
        "geolocated-at", "seen-at", "communicates-with", "contacted",
        "contacted-by", "mentions", "replies-to", "follows", "links-to",
        "redirects-to", "canonical-url-of", "hosts", "hosted-by", "served-by",
        "resolves-to", "ptr-to", "has-a", "has-aaaa", "has-cname", "has-ns",
        "has-mx", "has-txt", "has-spf", "has-dkim", "has-dmarc", "has-soa",
        "behind-cdn", "belongs-to-asn", "served-from", "shares-ip-with",
        "shares-asn-with", "hosts-service", "listens-on", "exposes-port", "runs",
        "runs-on", "leaked-in", "credential-for", "compromised-by", "observed-on",
        "observed-by", "collected-from", "extracted-from", "derived-from",
        "downloaded-from", "uploaded-to", "created-by", "modified-by", "hashes-to",
        "matches-hash", "evidence-of", "indicates", "attributed-to", "uses",
        "targets", "exploits", "mitigates", "c2-for", "in-scope-of",
        "out-of-scope-of", "discovered-by", "scanned-by", "has-finding",
        "vulnerable-to", "related-to",
    }
)


class DeterminismError(ValueError):
    pass


class RecursionRejected(DeterminismError):
    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category


@dataclass(frozen=True, slots=True)
class NormalizedUrl:
    url: str
    path: str
    query: str


@dataclass(frozen=True, slots=True)
class RecursionState:
    trace_id: str
    root_id: str
    parent_id: str
    actor: str
    actor_path: tuple[str, ...]
    depth: int
    hop: int
    max_depth: int
    max_hops: int
    run_id: str
    scan_id: str
    emitted_at: str


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def sha256_value(value: Any) -> str:
    return f"sha256:{sha256(canonical_json_bytes(value)).hexdigest()}"


def deterministic_document_id(dtype: str, identity: Mapping[str, Any]) -> str:
    normalized_dtype = _nonempty(dtype, "dtype").lower()
    digest = sha256(
        canonical_json_bytes(
            {"namespace": f"starintel:0.9:{normalized_dtype}", "identity": dict(identity)}
        )
    ).hexdigest()
    return f"{normalized_dtype}:{digest}"


def relation_document_id(
    dataset: str,
    source_id: str,
    predicate: str,
    target_id: str,
) -> str:
    fields = (
        _nonempty(dataset, "dataset"),
        _nonempty(source_id, "source_id"),
        normalize_predicate(predicate),
        _nonempty(target_id, "target_id"),
    )
    encoded = "|".join(
        f"{len(field.encode('utf-8'))}:{field}" for field in fields
    ).encode("utf-8")
    return f"relation:{sha256(encoded).hexdigest()}"


def normalize_domain_name(value: str) -> str:
    domain = _nonempty(value, "domain").lower()
    if domain.endswith("."):
        domain = domain[:-1]
    if not domain:
        raise DeterminismError("domain is empty after removing the DNS root dot")
    try:
        normalized = domain.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise DeterminismError(f"invalid internationalized domain: {value!r}") from exc
    if not normalized or normalized.endswith("."):
        raise DeterminismError(f"invalid domain: {value!r}")
    return normalized


def normalize_record_type(value: str) -> str:
    return _nonempty(value, "record_type").upper()


def normalize_ip_address(value: str) -> str:
    text = _nonempty(value, "ip")
    try:
        return str(ip_address(text))
    except ValueError as exc:
        raise DeterminismError(f"invalid IP address: {value!r}") from exc


def normalize_addresses(values: Sequence[str]) -> list[str]:
    unique = {normalize_ip_address(value) for value in values}
    return sorted(unique, key=lambda value: (ip_address(value).version, ip_address(value).packed))


def normalize_text(value: str, field: str) -> str:
    if not isinstance(value, str):
        raise DeterminismError(f"{field} must be a string")
    return " ".join(value.split())


def normalize_service(service: Mapping[str, Any]) -> JsonObject:
    if not isinstance(service, Mapping):
        raise DeterminismError("service must be an object")
    port = service.get("port")
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
        raise DeterminismError("service port must be an integer from 0 through 65535")
    name = _nonempty(service.get("name"), "service name").lower()
    version = normalize_text(service.get("version", ""), "service version")
    return {"port": port, "name": name, "version": version}


def normalize_services(services: Sequence[Mapping[str, Any]]) -> list[JsonObject]:
    normalized = {tuple(normalize_service(service).items()) for service in services}
    values = [dict(items) for items in normalized]
    values.sort(key=lambda value: (value["port"], value["name"], value["version"]))
    return values


def normalize_predicate(value: str) -> str:
    predicate = _nonempty(value, "predicate").lower()
    if predicate not in CANONICAL_PREDICATES:
        raise DeterminismError(f"noncanonical relation predicate: {value!r}")
    return predicate


def _normalize_percent(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        byte = int(match.group(1), 16)
        character = chr(byte)
        return character if character in _UNRESERVED else f"%{byte:02X}"

    return _PERCENT.sub(replace, value)


def normalize_url(value: str) -> NormalizedUrl:
    raw = _nonempty(value, "url")
    try:
        split = urlsplit(raw)
        port = split.port
    except ValueError as exc:
        raise DeterminismError(f"invalid URL: {value!r}") from exc
    scheme = split.scheme.lower()
    if not scheme or not split.netloc or split.hostname is None:
        raise DeterminismError("URL must be absolute and include a host")

    hostname = split.hostname
    try:
        if ":" in hostname:
            host = f"[{normalize_ip_address(hostname)}]"
        else:
            host = normalize_domain_name(hostname)
    except DeterminismError as exc:
        raise DeterminismError(f"invalid URL host: {hostname!r}") from exc

    userinfo = ""
    if split.username is not None:
        userinfo = _normalize_percent(split.username)
        if split.password is not None:
            userinfo += f":{_normalize_percent(split.password)}"
        userinfo += "@"

    default_port = (scheme == "http" and port == 80) or (
        scheme == "https" and port == 443
    )
    port_text = "" if port is None or default_port else f":{port}"
    path = split.path
    if not path and scheme in {"http", "https"}:
        path = "/"
    path = _normalize_percent(path)
    query = _normalize_percent(split.query)
    fragment = _normalize_percent(split.fragment)
    normalized = urlunsplit(
        SplitResult(scheme, f"{userinfo}{host}{port_text}", path, query, fragment)
    )
    return NormalizedUrl(normalized, path, query)


def logical_actor_name(value: str) -> str:
    return _WORKER_SUFFIX.sub("", _nonempty(value, "actor")).lower()


def stable_timestamp(document: Mapping[str, Any]) -> str:
    for field in ("date_updated", "date_added"):
        value = document.get(field)
        if isinstance(value, str) and value:
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError as exc:
                raise RecursionRejected(
                    "malformed_provenance", f"{field} must be RFC3339"
                ) from exc
            if parsed.tzinfo is None:
                raise RecursionRejected(
                    "malformed_provenance", f"{field} must include a timezone"
                )
            return value
    raise RecursionRejected(
        "malformed_provenance", "source document requires date_updated or date_added"
    )


def canonical_document_hash(document: Mapping[str, Any]) -> str:
    value = deepcopy(dict(document))
    value.pop("_rev", None)
    extensions = value.get("extensions")
    if isinstance(extensions, dict):
        star = extensions.get("star_server")
        if isinstance(star, dict):
            star.pop("output_hash", None)
    return sha256_value(value)


def rejection_run_id(document: Mapping[str, Any], actor: str) -> str:
    return deterministic_document_id(
        "run", {"actor": logical_actor_name(actor), "input_hash": canonical_document_hash(document)}
    )


def validate_recursion(document: Mapping[str, Any], actor: str) -> RecursionState:
    if not isinstance(document, Mapping):
        raise RecursionRejected("malformed_provenance", "source document must be an object")
    source_id = document.get("_id")
    if not isinstance(source_id, str) or not source_id:
        raise RecursionRejected("malformed_provenance", "source document requires _id")
    actor_name = logical_actor_name(actor)
    emitted_at = stable_timestamp(document)
    extensions = document.get("extensions", {})
    if extensions is None:
        extensions = {}
    if not isinstance(extensions, Mapping):
        raise RecursionRejected("malformed_provenance", "extensions must be an object")
    star = extensions.get("star_server")
    if star is None:
        star = {}
    if not isinstance(star, Mapping):
        raise RecursionRejected(
            "malformed_provenance", "extensions.star_server must be an object"
        )

    inherited = any(field in star for field in _PROVENANCE_FIELDS)
    if inherited:
        trace_id = _provenance_string(star, "trace_id")
        root_id = _provenance_string(star, "root_id")
        _provenance_optional_string(star, "parent_id")
        _provenance_string(star, "actor")
        path_value = star.get("actor_path")
        if not isinstance(path_value, list) or not all(
            isinstance(item, str) and item for item in path_value
        ):
            raise RecursionRejected(
                "malformed_provenance", "actor_path must be an array of non-empty strings"
            )
        actor_path = tuple(path_value)
        depth = _provenance_int(star, "depth", positive=False)
        hop = _provenance_int(star, "hop", positive=False)
        max_depth = _provenance_int(star, "max_depth", positive=True)
        max_hops = _provenance_int(star, "max_hops", positive=True)
        run_value = star.get("run_id")
        scan_value = star.get("scan_id")
        if run_value is None and scan_value is None:
            run_id = deterministic_document_id(
                "run", {"trace_id": trace_id, "root_id": root_id}
            )
            scan_id = run_id
        else:
            run_id = _nonempty_provenance(run_value, "run_id")
            scan_id = _nonempty_provenance(scan_value, "scan_id")
            if run_id != scan_id:
                raise RecursionRejected(
                    "malformed_provenance", "run_id and scan_id must match"
                )
    else:
        input_hash = canonical_document_hash(document)
        trace_id = deterministic_document_id("trace", {"input_hash": input_hash})
        root_id = source_id
        actor_path = ()
        depth = 0
        hop = 0
        max_depth = DEFAULT_MAX_DEPTH
        max_hops = DEFAULT_MAX_HOPS
        run_id = deterministic_document_id(
            "run", {"trace_id": trace_id, "root_id": root_id, "actor": actor_name}
        )
        scan_id = run_id

    if depth >= max_depth:
        raise RecursionRejected(
            "depth_budget_exceeded", f"depth {depth} reached max_depth {max_depth}"
        )
    if hop >= max_hops:
        raise RecursionRejected(
            "hop_budget_exceeded", f"hop {hop} reached max_hops {max_hops}"
        )
    if actor_name in actor_path:
        raise RecursionRejected(
            "actor_cycle", f"actor {actor_name!r} already appears in actor_path"
        )

    return RecursionState(
        trace_id=trace_id,
        root_id=root_id,
        parent_id=source_id,
        actor=actor_name,
        actor_path=(*actor_path, actor_name),
        depth=depth + 1,
        hop=hop + 1,
        max_depth=max_depth,
        max_hops=max_hops,
        run_id=run_id,
        scan_id=scan_id,
        emitted_at=emitted_at,
    )


def apply_provenance(
    source: Mapping[str, Any],
    output: Mapping[str, Any],
    actor: str,
    derivation: str,
) -> JsonObject:
    state = validate_recursion(source, actor)
    value = deepcopy(dict(output))
    source_extensions = source.get("extensions", {})
    extensions = deepcopy(dict(source_extensions)) if isinstance(source_extensions, Mapping) else {}
    output_extensions = value.get("extensions", {})
    if isinstance(output_extensions, Mapping):
        for namespace, namespace_value in output_extensions.items():
            if namespace != "star_server":
                extensions[namespace] = deepcopy(namespace_value)
    inherited_star = extensions.get("star_server", {})
    star = deepcopy(dict(inherited_star)) if isinstance(inherited_star, Mapping) else {}
    star.update(
        {
            "trace_id": state.trace_id,
            "root_id": state.root_id,
            "parent_id": state.parent_id,
            "actor": state.actor,
            "actor_path": list(state.actor_path),
            "depth": state.depth,
            "hop": state.hop,
            "max_depth": state.max_depth,
            "max_hops": state.max_hops,
            "derivation": _nonempty(derivation, "derivation"),
            "run_id": state.run_id,
            "scan_id": state.scan_id,
            "input_hash": canonical_document_hash(source),
            "emitted_at": state.emitted_at,
        }
    )
    star.pop("output_hash", None)
    extensions["star_server"] = star
    value["extensions"] = extensions
    star["output_hash"] = canonical_document_hash(value)
    return value


def _nonempty(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DeterminismError(f"{field} must be a non-empty string")
    return value.strip()


def _provenance_string(star: Mapping[str, Any], field: str) -> str:
    return _nonempty_provenance(star.get(field), field)


def _nonempty_provenance(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise RecursionRejected(
            "malformed_provenance", f"{field} must be a non-empty string"
        )
    return value


def _provenance_optional_string(star: Mapping[str, Any], field: str) -> str | None:
    value = star.get(field)
    if value is not None and (not isinstance(value, str) or not value):
        raise RecursionRejected(
            "malformed_provenance", f"{field} must be null or a non-empty string"
        )
    return value


def _provenance_int(star: Mapping[str, Any], field: str, *, positive: bool) -> int:
    value = star.get(field)
    valid = isinstance(value, int) and not isinstance(value, bool)
    if positive:
        valid = valid and value > 0
    else:
        valid = valid and value >= 0
    if not valid:
        qualifier = "positive" if positive else "non-negative"
        raise RecursionRejected(
            "malformed_provenance", f"{field} must be a {qualifier} integer"
        )
    return value
