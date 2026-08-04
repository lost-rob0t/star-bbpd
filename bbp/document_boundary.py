from __future__ import annotations

import json
import os
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from hashlib import md5
from importlib import metadata
from pathlib import Path
from typing import Any, Mapping, Sequence

import ulid
from starintel_doc import Document as StarIntelDocument
from starintel_doc import SPEC_VERSION, UnsupportedVersion, ValidationError

STARINTEL_DOC_VERSION = "0.9.0"
STARINTEL_DOC_COMMIT = "980f7a217a0d28f22508c2f05831ceb2fefe8bd0"
SCHEMA_VERSION = "0.9.0"
SCHEMA_REVISION = "0.9.0+fields.20260726.2"
SCHEMA_REPOSITORY = "lost-rob0t/starintel-gpt-auto-dig"
SCHEMA_COMMIT = "ff814ff63868286d68e21502122832802cd5e361"
SCHEMA_FILENAME = "starintel-doc-v0.9.0.schema.json"
SCHEMA_MANIFEST_FILENAME = "starintel-doc-v0.9.0.manifest.json"

JsonObject = dict[str, Any]
Source = dict[str, Any]
Service = dict[str, Any]


class DocumentBoundaryError(ValueError):
    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category


class SchemaRevisionMismatch(DocumentBoundaryError):
    def __init__(self, message: str) -> None:
        super().__init__("schema_revision_mismatch", message)


@dataclass(frozen=True, slots=True)
class SchemaContract:
    schema: JsonObject
    schema_path: Path
    manifest_path: Path
    schema_revision: str
    schema_commit: str


@dataclass(frozen=True, slots=True)
class CanonicalDocument:
    _document: StarIntelDocument

    @property
    def id(self) -> str:
        return self._document.value["_id"]

    @property
    def rev(self) -> str | None:
        value = self._document.value.get("_rev")
        return value if isinstance(value, str) else None

    @property
    def dtype(self) -> str:
        return self._document.value["dtype"]

    @property
    def dataset(self) -> str:
        return self._document.value["dataset"]

    @property
    def sources(self) -> list[Source]:
        return deepcopy(self._document.value["sources"])

    @property
    def extensions(self) -> JsonObject:
        return deepcopy(self._document.value.get("extensions", {}))

    @property
    def data(self) -> JsonObject:
        return deepcopy(self._document.value["data"])

    @property
    def source(self) -> str:
        return _required_string(self._document.value["data"], "source")

    @property
    def target(self) -> str:
        return _required_string(self._document.value["data"], "target")

    @property
    def predicate(self) -> str:
        return _required_string(self._document.value["data"], "predicate")

    def to_dict(self) -> JsonObject:
        return self._document.to_dict()

    def to_json(self, *, pretty: bool = False) -> str:
        return self._document.to_json(pretty=pretty)


@dataclass(frozen=True, slots=True)
class TargetDocument(CanonicalDocument):
    @property
    def actor(self) -> str:
        return _required_string(self._document.value["data"], "actor")

    @property
    def target(self) -> str:
        return _required_string(self._document.value["data"], "target")

    @property
    def delay(self) -> int:
        value = self._document.value["data"].get("delay", 0)
        return value if isinstance(value, int) and not isinstance(value, bool) else 0

    @property
    def recurring(self) -> bool:
        value = self._document.value["data"].get("recurring", False)
        return value if isinstance(value, bool) else False

    @property
    def options(self) -> list[Any]:
        value = self._document.value["data"].get("options", [])
        return deepcopy(value) if isinstance(value, list) else []


def _required_string(value: Mapping[str, Any], field: str) -> str:
    item = value.get(field)
    if not isinstance(item, str) or not item:
        raise DocumentBoundaryError(
            "missing_required_field", f"$.data.{field}: expected non-empty string"
        )
    return item


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _schema_paths(
    schema_path: str | os.PathLike[str] | None,
    manifest_path: str | os.PathLike[str] | None,
) -> tuple[Path, Path]:
    if schema_path is not None:
        schema = Path(schema_path)
    elif explicit := os.environ.get("STARINTEL_SCHEMA"):
        schema = Path(explicit)
    elif root := os.environ.get("STARINTEL_CONFORMANCE_ROOT"):
        schema = Path(root) / "schemas" / SCHEMA_FILENAME
    else:
        schema = Path.cwd() / "schemas" / SCHEMA_FILENAME
    manifest = (
        Path(manifest_path)
        if manifest_path is not None
        else schema.with_name(SCHEMA_MANIFEST_FILENAME)
    )
    return schema, manifest


def _read_object(path: Path, label: str) -> JsonObject:
    if not path.is_file():
        raise SchemaRevisionMismatch(f"{label} not found: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SchemaRevisionMismatch(f"cannot read {label} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SchemaRevisionMismatch(f"{label} must be a JSON object: {path}")
    return value


def verify_package_contract(package_version: str | None = None) -> None:
    if SPEC_VERSION != SCHEMA_VERSION:
        raise SchemaRevisionMismatch(
            f"starintel_doc reports spec {SPEC_VERSION!r}; expected {SCHEMA_VERSION!r}"
        )
    if package_version is None:
        try:
            package_version = metadata.version("starintel_doc")
        except metadata.PackageNotFoundError:
            return
    if package_version != STARINTEL_DOC_VERSION:
        raise SchemaRevisionMismatch(
            f"starintel_doc package {package_version!r}; expected {STARINTEL_DOC_VERSION!r}"
        )


@lru_cache(maxsize=8)
def load_schema_contract(
    schema_path: str | os.PathLike[str] | None = None,
    manifest_path: str | os.PathLike[str] | None = None,
) -> SchemaContract:
    verify_package_contract()
    schema_file, manifest_file = _schema_paths(schema_path, manifest_path)
    schema = _read_object(schema_file, "StarIntel schema")
    manifest = _read_object(manifest_file, "StarIntel schema manifest")
    actual_version = manifest.get("schema_version")
    actual_revision = manifest.get("schema_revision")
    if actual_version != SCHEMA_VERSION or actual_revision != SCHEMA_REVISION:
        raise SchemaRevisionMismatch(
            "schema manifest mismatch: "
            f"version={actual_version!r}, revision={actual_revision!r}; "
            f"expected version={SCHEMA_VERSION!r}, revision={SCHEMA_REVISION!r}"
        )
    return SchemaContract(
        schema=schema,
        schema_path=schema_file,
        manifest_path=manifest_file,
        schema_revision=actual_revision,
        schema_commit=SCHEMA_COMMIT,
    )


def _schema(contract: SchemaContract | None) -> JsonObject:
    return (contract or load_schema_contract()).schema


def _wrap(
    value: JsonObject, contract: SchemaContract | None = None
) -> CanonicalDocument:
    try:
        return CanonicalDocument(StarIntelDocument.from_dict(value, _schema(contract)))
    except (UnsupportedVersion, ValidationError) as exc:
        raise DocumentBoundaryError(exc.category, str(exc)) from exc


def decode_document(
    payload: str | bytes,
    *,
    expected_dtype: str | None = None,
    contract: SchemaContract | None = None,
) -> CanonicalDocument:
    if isinstance(payload, bytes):
        try:
            payload = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise DocumentBoundaryError("invalid_encoding", "payload is not UTF-8") from exc
    if not isinstance(payload, str):
        raise DocumentBoundaryError("wrong_type", "payload must be text or bytes")
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise DocumentBoundaryError("invalid_json", f"invalid JSON at offset {exc.pos}") from exc
    if not isinstance(parsed, dict):
        raise DocumentBoundaryError("wrong_type", "$: expected object")
    document = _wrap(parsed, contract)
    if expected_dtype is not None and document.dtype != expected_dtype:
        raise DocumentBoundaryError(
            "unexpected_dtype",
            f"$.dtype: expected {expected_dtype!r}, got {document.dtype!r}",
        )
    return document


def decode_target(
    payload: str | bytes, *, contract: SchemaContract | None = None
) -> TargetDocument:
    document = decode_document(payload, expected_dtype="target", contract=contract)
    target = TargetDocument(document._document)
    _ = target.actor
    _ = target.target
    return target


def decode_domain(
    payload: str | bytes, *, contract: SchemaContract | None = None
) -> CanonicalDocument:
    document = decode_document(payload, expected_dtype="domain", contract=contract)
    _ = domain_name(document)
    return document


def target_dataset(target: TargetDocument) -> str:
    return target.dataset


def derived_sources(target: TargetDocument, tool_name: str) -> list[Source]:
    if not isinstance(tool_name, str) or not tool_name:
        raise DocumentBoundaryError("wrong_type", "tool_name must be a non-empty string")
    sources = target.sources
    tool_source: Source = {"kind": "tool", "name": tool_name}
    if tool_source not in sources:
        sources.append(tool_source)
    return sources


def inherited_extensions(target: TargetDocument) -> JsonObject:
    return target.extensions


def _legacy_hash_id(*values: object) -> str:
    return md5("".join(map(str, values)).encode("utf-8")).hexdigest()


def _build(
    *,
    dtype: str,
    document_id: str,
    dataset: str,
    data: JsonObject,
    sources: Sequence[Source],
    extensions: Mapping[str, Any],
    contract: SchemaContract | None,
    now: str | None = None,
) -> CanonicalDocument:
    timestamp = now or _utc_now()
    return _wrap(
        {
            "_id": document_id,
            "dataset": dataset,
            "dtype": dtype,
            "schema_version": SCHEMA_VERSION,
            "version": 1,
            "date_added": timestamp,
            "date_updated": timestamp,
            "sources": deepcopy(list(sources)),
            "evidence": [],
            "data": deepcopy(data),
            "extensions": deepcopy(dict(extensions)),
        },
        contract,
    )


def build_target(
    actor: str,
    target_value: str,
    dataset: str,
    *,
    sources: Sequence[Source] = (),
    extensions: Mapping[str, Any] | None = None,
    delay: int = 0,
    recurring: bool = False,
    options: Sequence[Any] = (),
    contract: SchemaContract | None = None,
    now: str | None = None,
) -> TargetDocument:
    for field, value in (
        ("actor", actor),
        ("target_value", target_value),
        ("dataset", dataset),
    ):
        if not isinstance(value, str) or not value:
            raise DocumentBoundaryError("wrong_type", f"{field} must be a non-empty string")
    if isinstance(delay, bool) or not isinstance(delay, int):
        raise DocumentBoundaryError("wrong_type", "delay must be an integer")
    if not isinstance(recurring, bool):
        raise DocumentBoundaryError("wrong_type", "recurring must be a boolean")
    document = _build(
        dtype="target",
        document_id=_legacy_hash_id(dataset, target_value, actor),
        dataset=dataset,
        data={
            "actor": actor,
            "target": target_value,
            "delay": delay,
            "recurring": recurring,
            "options": deepcopy(list(options)),
        },
        sources=sources,
        extensions=extensions or {},
        contract=contract,
        now=now,
    )
    return TargetDocument(document._document)


def build_domain(
    domain: str,
    target: TargetDocument,
    tool_name: str,
    *,
    record_type: str = "A",
    resolved_addresses: Sequence[str] = (),
    contract: SchemaContract | None = None,
    now: str | None = None,
) -> CanonicalDocument:
    if not isinstance(domain, str) or not domain.strip():
        raise DocumentBoundaryError("wrong_type", "domain must be a non-empty string")
    value = domain.strip()
    return _build(
        dtype="domain",
        document_id=_legacy_hash_id(value, record_type),
        dataset=target.dataset,
        data={
            "domain": value,
            "record_type": record_type,
            "record": value,
            "resolved_addresses": list(resolved_addresses),
        },
        sources=derived_sources(target, tool_name),
        extensions=inherited_extensions(target),
        contract=contract,
        now=now,
    )


def build_service(port: int, name: str, version: str = "") -> Service:
    if isinstance(port, bool) or not isinstance(port, int):
        raise DocumentBoundaryError("wrong_type", "service port must be an integer")
    if not 0 <= port <= 65535:
        raise DocumentBoundaryError("out_of_range", "service port must be between 0 and 65535")
    if not isinstance(name, str) or not name:
        raise DocumentBoundaryError("wrong_type", "service name must be a non-empty string")
    if not isinstance(version, str):
        raise DocumentBoundaryError("wrong_type", "service version must be a string")
    return {"port": port, "name": name, "version": version}


def build_host(
    hostname: str,
    ip: str,
    services: Sequence[Service],
    target: TargetDocument,
    tool_name: str,
    *,
    os_name: str = "",
    contract: SchemaContract | None = None,
    now: str | None = None,
) -> CanonicalDocument:
    if not isinstance(hostname, str):
        raise DocumentBoundaryError("wrong_type", "hostname must be a string")
    if not isinstance(ip, str) or not ip:
        raise DocumentBoundaryError("wrong_type", "ip must be a non-empty string")
    return _build(
        dtype="host",
        document_id=_legacy_hash_id(ip),
        dataset=target.dataset,
        data={
            "hostname": hostname,
            "ip": ip,
            "ports": deepcopy(list(services)),
            "os": os_name,
        },
        sources=derived_sources(target, tool_name),
        extensions=inherited_extensions(target),
        contract=contract,
        now=now,
    )


def build_url(
    url: str,
    target: TargetDocument,
    tool_name: str,
    *,
    path: str = "",
    query: str = "",
    content: str = "",
    contract: SchemaContract | None = None,
    now: str | None = None,
) -> CanonicalDocument:
    if not isinstance(url, str) or not url:
        raise DocumentBoundaryError("wrong_type", "url must be a non-empty string")
    return _build(
        dtype="url",
        document_id=_legacy_hash_id(url, content),
        dataset=target.dataset,
        data={"url": url, "path": path, "query": query, "content": content},
        sources=derived_sources(target, tool_name),
        extensions=inherited_extensions(target),
        contract=contract,
        now=now,
    )


def build_relation(
    source_id: str,
    target_id: str,
    predicate: str,
    note: str,
    target: TargetDocument,
    tool_name: str,
    *,
    contract: SchemaContract | None = None,
    now: str | None = None,
) -> CanonicalDocument:
    for field, value in (
        ("source_id", source_id),
        ("target_id", target_id),
        ("predicate", predicate),
    ):
        if not isinstance(value, str) or not value:
            raise DocumentBoundaryError("wrong_type", f"{field} must be a non-empty string")
    return _build(
        dtype="relation",
        document_id=str(ulid.new()),
        dataset=target.dataset,
        data={
            "subject": source_id,
            "predicate": predicate,
            "object": target_id,
            "source": source_id,
            "target": target_id,
            "directed": True,
            "note": note,
        },
        sources=derived_sources(target, tool_name),
        extensions=inherited_extensions(target),
        contract=contract,
        now=now,
    )


def domain_name(document: CanonicalDocument) -> str:
    if document.dtype != "domain":
        raise DocumentBoundaryError("unexpected_dtype", "expected domain document")
    return _required_string(document._document.value["data"], "domain")


def with_domain_addresses(
    document: CanonicalDocument,
    addresses: Sequence[str],
    *,
    contract: SchemaContract | None = None,
    now: str | None = None,
) -> CanonicalDocument:
    if document.dtype != "domain":
        raise DocumentBoundaryError("unexpected_dtype", "expected domain document")
    value = document.to_dict()
    value["data"]["resolved_addresses"] = list(addresses)
    value["date_updated"] = now or _utc_now()
    return _wrap(value, contract)


def serialize_document(document: CanonicalDocument, *, pretty: bool = False) -> str:
    return document.to_json(pretty=pretty)
