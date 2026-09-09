from __future__ import annotations

import json
import urllib.request

from bbp.config import Config
from bbp.manifest_http import ActorManifestHttpServer, start_manifest_http


def config(*, enabled: bool = True) -> Config:
    return Config(
        {
            "service": {
                "name": "bbp-http-test",
                "manifest_http_enabled": enabled,
                "manifest_http_host": "127.0.0.1",
                "manifest_http_port": 0,
            },
            "documents": {"host": "127.0.0.1", "port": 5672, "exchange": "documents"},
            "events": {"exchange": "events"},
            "routing": {
                "target_key": "actors.{actor}.new.target",
                "document_ingest_key": "documents.ingest.{dtype}",
                "document_update_key": "documents.updated.{dtype}",
                "document_new_key": "documents.new.{dtype}",
                "event_key": "event.{eventType}",
            },
            "actors": {
                "httpx": {"enabled": True, "queue": "bbp.httpx.targets", "prefetch": 1},
            },
            "tools": {
                "httpx": {"timeout_sec": 30, "publish_timeout_sec": 10},
            },
        }
    )


def get_json(server: ActorManifestHttpServer, path: str) -> dict:
    with urllib.request.urlopen(f"http://{server.host}:{server.port}{path}", timeout=2) as response:
        return json.loads(response.read().decode("utf-8"))


def test_manifest_http_serves_health_and_actor_manifests() -> None:
    server = start_manifest_http(config())
    assert server is not None
    try:
        health = get_json(server, "/healthz")
        assert health == {"service": "bbp-http-test", "status": "ok"}
        document = get_json(server, "/api/v1/actors")
        assert document["status"] == "ok"
        assert [actor["id"] for actor in document["data"]["actors"]] == ["httpx"]
    finally:
        server.stop()


def test_manifest_http_is_optional() -> None:
    assert start_manifest_http(config(enabled=False)) is None
