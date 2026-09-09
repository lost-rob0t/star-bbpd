from __future__ import annotations

from typing import Any

from bbp.config import Config


ACTOR_METADATA: dict[str, tuple[str, str]] = {
    "subfinder": ("Subfinder", "Discover subdomains for a target domain."),
    "nmap": ("Nmap", "Probe target hosts and services with Nmap."),
    "httpx": ("HTTPX", "Probe target HTTP services and emit normalized URL findings."),
    "katana": ("Katana", "Crawl target HTTP services and emit discovered URLs."),
}


def _actor_label(actor_name: str) -> str:
    metadata = ACTOR_METADATA.get(actor_name)
    if metadata:
        return metadata[0]
    return actor_name.replace("-", " ").replace("_", " ").title()


def _actor_description(actor_name: str) -> str:
    metadata = ACTOR_METADATA.get(actor_name)
    if metadata:
        return metadata[1]
    return f"Remote StarIntel actor provided by star-bbpd: {actor_name}."


def build_actor_manifest(config: Config, actor_name: str) -> dict[str, Any]:
    actor_config = config.get_actor_config(actor_name)
    service_name = str(config.service.get("name", "bbp-actors"))
    routing_key = config.routing["target_key"].format(actor=actor_name)
    return {
        "schema": "starintel-actor-deployment-manifest-v1",
        "id": actor_name,
        "label": _actor_label(actor_name),
        "description": _actor_description(actor_name),
        "version": 1,
        "available": True,
        "service": {
            "id": service_name,
            "kind": "star-bbpd",
            "language": "python",
        },
        "runtime": {
            "location": "remote",
            "transport": "rabbitmq",
            "routing_key": routing_key,
            "queue": str(actor_config.get("queue", "")),
        },
        "dispatch": {
            "dtype": "target",
            "actor": actor_name,
        },
    }


def build_actor_manifests(config: Config) -> list[dict[str, Any]]:
    return [build_actor_manifest(config, name) for name in sorted(config.enabled_actors())]


def actor_manifest_document(config: Config) -> dict[str, Any]:
    return {
        "status": "ok",
        "data": {
            "schema": "starintel-actor-deployment-manifest-list-v1",
            "service": str(config.service.get("name", "bbp-actors")),
            "actors": build_actor_manifests(config),
        },
    }
