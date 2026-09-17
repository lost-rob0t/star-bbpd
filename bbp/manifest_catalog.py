from __future__ import annotations

from typing import Any


def _schema(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
    }


def register_extended_manifests() -> None:
    from bbp.manifests import ACTOR_MANIFESTS, ActorManifestSpec

    ACTOR_MANIFESTS.update(
        {
            "bbot": ActorManifestSpec(
                actor_id="bbot",
                actor_type="recon-orchestrator",
                entrypoint="bbp.tools.bbot:BbotActor",
                runtime="python-pykka-threading-bbot3",
                operations=("scan-target",),
                input_dtypes=("target",),
                output_dtypes=("domain", "host", "url", "observation", "relation"),
                dependencies=("bbot>=3", "pykka", "pika"),
                capabilities=(
                    "bbot-json-events",
                    "module-allowlist",
                    "scope-preserving-discovery",
                    "deterministic-documents",
                ),
                target_options=(
                    {"key": "modules", "type": "array", "items": {"type": "string"}},
                ),
                configuration_schema=_schema(
                    {
                        "modules": {"type": "array", "items": {"type": "string"}},
                        "allowed_modules": {"type": "array", "items": {"type": "string"}},
                        "timeout_sec": {"type": "number", "exclusiveMinimum": 0},
                    }
                ),
                lifecycle={
                    "startup": "publish manifest, bind durable target consumer, then report ready",
                    "shutdown": "stop intake and terminate bounded BBOT subprocesses",
                },
                authorization={"scope": "explicit StarIntel target scope"},
            ),
            "nuclei": ActorManifestSpec(
                actor_id="nuclei",
                actor_type="detection-scanner",
                entrypoint="bbp.tools.nuclei:NucleiActor",
                runtime="python-pykka-threading-nuclei",
                operations=("detect-template-matches",),
                input_dtypes=("target",),
                output_dtypes=("observation", "relation"),
                dependencies=("nuclei", "pykka", "pika"),
                capabilities=(
                    "nuclei-jsonl",
                    "authorized-scope-gate",
                    "template-allowlist",
                    "raw-request-omission",
                    "oast-disabled-by-default",
                ),
                target_options=(
                    {"key": "authorized_scope", "type": "boolean", "required": True},
                    {"key": "tags", "type": "array", "items": {"type": "string"}},
                    {"key": "templates", "type": "array", "items": {"type": "string"}},
                ),
                configuration_schema=_schema(
                    {
                        "allowed_tags": {"type": "array", "items": {"type": "string"}},
                        "allowed_templates": {"type": "array", "items": {"type": "string"}},
                        "exclude_tags": {"type": "array", "items": {"type": "string"}},
                        "rate_limit": {"type": "integer", "minimum": 1},
                        "concurrency": {"type": "integer", "minimum": 1},
                        "bulk_size": {"type": "integer", "minimum": 1},
                    }
                ),
                lifecycle={
                    "startup": "publish manifest, bind durable target consumer, then report ready",
                    "shutdown": "stop intake and terminate bounded Nuclei subprocesses",
                },
                authorization={"scope": "authorized_scope target option plus server policy"},
            ),
            "http-proxy": ActorManifestSpec(
                actor_id="http-proxy",
                actor_type="network-capture",
                entrypoint="bbp.tools.http_proxy:HttpProxyActor",
                runtime="python-pykka-threading-http-capture",
                operations=("capture-http-transaction",),
                input_dtypes=("target",),
                output_dtypes=("http-transaction", "relation"),
                dependencies=("python-stdlib-http", "pykka", "pika", "starintel-doc>=0.9.2"),
                capabilities=(
                    "http-transaction-profile-0.9.2",
                    "bounded-body-artifacts",
                    "sensitive-header-redaction",
                    "static-operator-proxy",
                    "challenge-observation",
                ),
                target_options=(
                    {"key": "method", "type": "string", "enum": ["GET", "HEAD"]},
                    {"key": "proxy_actor_uri", "type": "string"},
                    {"key": "challenge_actor_uri", "type": "string"},
                ),
                configuration_schema=_schema(
                    {
                        "proxy_env": {"type": "string", "writeOnly": True},
                        "max_body_bytes": {"type": "integer", "minimum": 0},
                        "artifact_dir": {"type": "string"},
                        "artifact_uri_prefix": {"type": "string"},
                        "timeout_sec": {"type": "number", "exclusiveMinimum": 0},
                    }
                ),
                lifecycle={
                    "startup": "publish manifest, bind durable target consumer, then report ready",
                    "shutdown": "stop intake and finish bounded transaction capture",
                },
                authorization={
                    "credentials": "optional proxy URL from operator-owned environment only"
                },
            ),
            "screenshot": ActorManifestSpec(
                actor_id="screenshot",
                actor_type="browser-capture",
                entrypoint="bbp.tools.screenshot:ScreenshotActor",
                runtime="python-pykka-threading-gowitness",
                operations=("capture-screenshot",),
                input_dtypes=("target",),
                output_dtypes=("web-capture", "relation"),
                dependencies=("gowitness>=3", "chromium", "pykka", "pika", "starintel-doc>=0.9.2"),
                capabilities=(
                    "web-capture-profile-0.9.2",
                    "artifact-backed-screenshot",
                    "challenge-observation",
                    "static-operator-proxy",
                ),
                target_options=(
                    {"key": "proxy_actor_uri", "type": "string"},
                    {"key": "challenge_actor_uri", "type": "string"},
                ),
                configuration_schema=_schema(
                    {
                        "artifact_dir": {"type": "string"},
                        "artifact_uri_prefix": {"type": "string"},
                        "proxy_url": {"type": "string", "writeOnly": True},
                        "page_timeout_sec": {"type": "integer", "minimum": 1},
                        "delay_sec": {"type": "integer", "minimum": 0},
                    }
                ),
                lifecycle={
                    "startup": "publish manifest, bind durable target consumer, then report ready",
                    "shutdown": "stop intake and terminate bounded browser captures",
                },
                authorization={
                    "credentials": "optional static proxy supplied only in operator configuration"
                },
            ),
        }
    )
