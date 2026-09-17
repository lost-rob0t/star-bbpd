from __future__ import annotations

import argparse
import hashlib
import json
import re
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Iterable

from bbp.document_boundary import RELEASE_VERSION, SCHEMA_REVISION, SCHEMA_VERSION
from bbp.publisher_pool import PublishDocument
from bbp.settlement import RequiredPublishError

if TYPE_CHECKING:
    from bbp.config import Config
    from bbp.publisher_pool import PublisherPoolProxy

MANIFEST_PROTOCOL_VERSION = "1.1.0"
IMPLEMENTATION_REPOSITORY = "lost-rob0t/star-bbpd"
IMPLEMENTATION_VERSION = "0.1.0"
MANIFEST_DTYPE = "actor-manifest"
MANIFEST_DATASET = "starintel-system"

JsonObject = dict[str, Any]


@dataclass(frozen=True, slots=True)
class ActorManifestSpec:
    actor_id: str
    actor_type: str
    entrypoint: str
    runtime: str
    operations: tuple[str, ...]
    input_dtypes: tuple[str, ...]
    output_dtypes: tuple[str, ...]
    dependencies: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    target_options: tuple[JsonObject, ...] = ()
    configuration_schema: JsonObject = field(default_factory=dict)
    lifecycle: JsonObject = field(default_factory=dict)
    authorization: JsonObject = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ActorManifestDocument:
    value: JsonObject

    @property
    def id(self) -> str:
        return str(self.value["_id"])

    @property
    def dtype(self) -> str:
        return str(self.value["dtype"])

    def to_dict(self) -> JsonObject:
        return deepcopy(self.value)

    def to_json(self, *, pretty: bool = False) -> str:
        if pretty:
            return json.dumps(self.value, indent=2, sort_keys=True)
        return json.dumps(self.value, separators=(",", ":"), sort_keys=True)


def _config_schema() -> JsonObject:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "queue": {"type": "string"},
            "prefetch": {"type": "integer", "minimum": 1},
            "timeout_sec": {"type": "number", "exclusiveMinimum": 0},
            "publish_timeout_sec": {"type": "number", "exclusiveMinimum": 0},
            "args": {"type": "array", "items": {"type": "string"}},
        },
    }


ACTOR_MANIFESTS: dict[str, ActorManifestSpec] = {
    "subfinder": ActorManifestSpec(
        actor_id="subfinder",
        actor_type="recon-scanner",
        entrypoint="bbp.tools.subfinder:SubfinderActor",
        runtime="python-pykka-threading-rabbitmq",
        operations=("scan-domain",),
        input_dtypes=("target",),
        output_dtypes=("domain", "relation"),
        dependencies=("subfinder", "pykka", "pika"),
        capabilities=("subdomain-enumeration", "deterministic-documents", "recursive-provenance"),
        configuration_schema=_config_schema(),
        lifecycle={"startup": "publish manifest, bind durable target consumer, then report ready", "shutdown": "stop intake, settle in-flight scans, then stop actor pool"},
        authorization={"credentials": "tool/provider configuration external to manifest"},
    ),
    "nmap": ActorManifestSpec(
        actor_id="nmap",
        actor_type="recon-scanner",
        entrypoint="bbp.tools.nmap:NmapActor",
        runtime="python-pykka-threading-rabbitmq",
        operations=("scan-host",),
        input_dtypes=("target",),
        output_dtypes=("host", "relation"),
        dependencies=("nmap", "pykka", "pika"),
        capabilities=("service-enumeration", "deterministic-documents", "recursive-provenance"),
        configuration_schema=_config_schema(),
        lifecycle={"startup": "publish manifest, bind durable target consumer, then report ready", "shutdown": "stop intake, settle in-flight scans, then stop actor pool"},
        authorization={"credentials": "none"},
    ),
    "httpx": ActorManifestSpec(
        actor_id="httpx",
        actor_type="recon-scanner",
        entrypoint="bbp.tools.httpx:HttpxActor",
        runtime="python-pykka-threading-rabbitmq",
        operations=("probe-http",),
        input_dtypes=("target",),
        output_dtypes=("url", "relation"),
        dependencies=("httpx", "pykka", "pika"),
        capabilities=("http-probing", "deterministic-documents", "recursive-provenance"),
        configuration_schema=_config_schema(),
        lifecycle={"startup": "publish manifest, bind durable target consumer, then report ready", "shutdown": "stop intake, settle in-flight scans, then stop actor pool"},
        authorization={"credentials": "none"},
    ),
    "katana": ActorManifestSpec(
        actor_id="katana",
        actor_type="recon-scanner",
        entrypoint="bbp.tools.katana:KatanaActor",
        runtime="python-pykka-threading-rabbitmq",
        operations=("crawl-url",),
        input_dtypes=("target",),
        output_dtypes=("url", "relation"),
        dependencies=("katana", "pykka", "pika"),
        capabilities=("web-crawl", "deterministic-documents", "recursive-provenance"),
        configuration_schema=_config_schema(),
        lifecycle={"startup": "publish manifest, bind durable target consumer, then report ready", "shutdown": "stop intake, settle in-flight scans, then stop actor pool"},
        authorization={"credentials": "none"},
    ),
    "dns-resolver": ActorManifestSpec(
        actor_id="dns-resolver",
        actor_type="enricher",
        entrypoint="bbp.dns_resolver:DnsResolverActor",
        runtime="python-pykka-threading-rabbitmq",
        operations=("resolve-domain",),
        input_dtypes=("domain",),
        output_dtypes=("domain",),
        dependencies=("dnspython", "pykka", "pika"),
        capabilities=("dns-resolution", "document-update", "recursive-provenance"),
        configuration_schema=_config_schema(),
        lifecycle={"startup": "publish manifest, bind durable domain consumer, then report ready", "shutdown": "stop intake, settle the active lookup, then stop actor"},
        authorization={"credentials": "none"},
    ),
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def canonical_authority(authority: str) -> str:
    if not isinstance(authority, str) or not authority.strip():
        raise ValueError("STAR authority must be a non-empty string")
    value = authority.strip().lower()
    if any(char in value for char in "/:@?#") or any(char.isspace() for char in value):
        raise ValueError("STAR authority must be an RFC 3986 reg-name without userinfo or port")
    if value in {".", ".."} or not re.fullmatch(r"[a-z0-9._~-]+", value):
        raise ValueError(f"unsupported STAR authority reg-name: {authority!r}")
    return value


def actor_resource_uri(authority: str, actor_id: str) -> str:
    canonical = canonical_authority(authority)
    if actor_id not in ACTOR_MANIFESTS:
        raise KeyError(f"unknown BBPD actor manifest: {actor_id}")
    return f"star://{canonical}/actor/{actor_id}"


def _semantic_contract(spec: ActorManifestSpec) -> JsonObject:
    return {
        "resource_kind": "actor",
        "name": spec.actor_id,
        "version": IMPLEMENTATION_VERSION,
        "actor_type": spec.actor_type,
        "operations": list(spec.operations),
        "input_dtypes": list(spec.input_dtypes),
        "output_dtypes": list(spec.output_dtypes),
        "capabilities": list(spec.capabilities),
    }


def semantic_digest(spec: ActorManifestSpec) -> str:
    payload = json.dumps(_semantic_contract(spec), separators=(",", ":"), sort_keys=True)
    return f"sha256:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


def _deployment_binding(config: Config, actor_id: str) -> tuple[str, tuple[str, ...]]:
    if actor_id == "dns-resolver":
        actor_config = config.data.get("dns_resolver", {})
        queue = actor_config.get("queue", "bbp.dns-resolver.domains")
        routing_keys = (
            config.routing["document_new_key"].format(dtype="domain"),
            config.routing["document_update_key"].format(dtype="domain"),
        )
        return queue, routing_keys
    actor_config = config.get_actor_config(actor_id)
    queue = actor_config.get("queue", f"bbp.{actor_id}.targets")
    routing_keys = (
        config.routing["target_key"].format(actor=actor_id),
        *(config.routing["document_ingest_key"].format(dtype=dtype) for dtype in ACTOR_MANIFESTS[actor_id].output_dtypes),
    )
    return queue, routing_keys


def actor_manifest_document(config: Config, actor_id: str, *, now: str | None = None) -> ActorManifestDocument:
    try:
        spec = ACTOR_MANIFESTS[actor_id]
    except KeyError as exc:
        raise KeyError(f"unknown BBPD actor manifest: {actor_id}") from exc
    timestamp = now or _utc_now()
    configured_authority = config.service.get("star_authority")
    if not isinstance(configured_authority, str) or not configured_authority.strip():
        raise ValueError("service.star_authority is required when emitting actor manifests")
    resource_uri = actor_resource_uri(configured_authority, actor_id)
    consumer_path, routing_keys = _deployment_binding(config, actor_id)
    semantic = _semantic_contract(spec)
    semantic["digest"] = semantic_digest(spec)
    data: JsonObject = {
        "actor": spec.actor_id,
        "consumer_path": consumer_path,
        "target_options": [deepcopy(option) for option in spec.target_options],
        "manifest_type": "actor-runtime",
        "name": spec.actor_id,
        "generated_at": timestamp,
        "schema_versions": [SCHEMA_VERSION],
    }
    contract: JsonObject = {
        "protocol_version": MANIFEST_PROTOCOL_VERSION,
        "resourceUri": resource_uri,
        "resource_kind": "actor",
        "semantic": semantic,
        "actor_id": spec.actor_id,
        "actor_type": spec.actor_type,
        "implementation": {"repository": IMPLEMENTATION_REPOSITORY, "version": IMPLEMENTATION_VERSION},
        "entrypoint": spec.entrypoint,
        "runtime": spec.runtime,
        "operations": list(spec.operations),
        "input_dtypes": list(spec.input_dtypes),
        "output_dtypes": list(spec.output_dtypes),
        "dependencies": list(spec.dependencies),
        "capabilities": list(spec.capabilities),
        "configuration_schema": deepcopy(spec.configuration_schema),
        "lifecycle": deepcopy(spec.lifecycle),
        "authorization": deepcopy(spec.authorization),
        "deployment": {"transport": "rabbitmq", "consumer_path": consumer_path, "routing_keys": list(routing_keys)},
        "schema_release": RELEASE_VERSION,
        "schema_revision": SCHEMA_REVISION,
    }
    return ActorManifestDocument({
        "_id": f"starintel:actor-manifest:bbp:{spec.actor_id}",
        "dataset": MANIFEST_DATASET,
        "dtype": MANIFEST_DTYPE,
        "schema_version": SCHEMA_VERSION,
        "version": 1,
        "date_added": timestamp,
        "date_updated": timestamp,
        "sources": [],
        "evidence": [],
        "data": data,
        "extensions": {"starintel.actor_manifest.v1": contract},
    })


def enabled_actor_ids(config: Config) -> tuple[str, ...]:
    actor_ids = list(config.enabled_actors())
    if config.data.get("dns_resolver", {}).get("enabled", False):
        actor_ids.append("dns-resolver")
    unknown = sorted(set(actor_ids).difference(ACTOR_MANIFESTS))
    if unknown:
        raise ValueError(f"enabled actors lack manifests: {', '.join(unknown)}")
    return tuple(sorted(set(actor_ids)))


def enabled_actor_manifest_documents(config: Config, *, now: str | None = None) -> tuple[ActorManifestDocument, ...]:
    timestamp = now or _utc_now()
    return tuple(actor_manifest_document(config, actor_id, now=timestamp) for actor_id in enabled_actor_ids(config))


def publish_actor_manifests(config: Config, publisher: PublisherPoolProxy, documents: Iterable[ActorManifestDocument] | None = None) -> int:
    enabled = config.service.get("publish_actor_manifests", True)
    if not isinstance(enabled, bool):
        raise ValueError("service.publish_actor_manifests must be a boolean")
    if not enabled:
        return 0
    selected = tuple(documents) if documents is not None else enabled_actor_manifest_documents(config)
    routing_key = config.routing["document_ingest_key"].format(dtype=MANIFEST_DTYPE)
    timeout = float(config.service.get("publish_settlement_timeout_sec", 30))
    for document in selected:
        result = publisher.publish_required(PublishDocument(document, routing_key), timeout)
        if not result.ok:
            raise RequiredPublishError(result)
    return len(selected)


def _selected_documents(config: Config, actor_ids: list[str]) -> tuple[ActorManifestDocument, ...]:
    if not actor_ids:
        return enabled_actor_manifest_documents(config)
    unknown = sorted(set(actor_ids).difference(ACTOR_MANIFESTS))
    if unknown:
        raise ValueError(f"unknown actor manifest(s): {', '.join(unknown)}")
    return tuple(actor_manifest_document(config, actor_id) for actor_id in actor_ids)


def main() -> None:
    from bbp.config import Config
    parser = argparse.ArgumentParser(description="Emit StarIntel actor manifests for star-bbpd")
    parser.add_argument("config", nargs="?", default="config.toml")
    parser.add_argument("--actor", action="append", default=[], help="emit only this actor id; repeat for multiple actors")
    args = parser.parse_args()
    config = Config.load(args.config)
    try:
        documents = _selected_documents(config, args.actor)
    except ValueError as exc:
        parser.error(str(exc))
    for document in documents:
        print(document.to_json())


if __name__ == "__main__":
    main()
