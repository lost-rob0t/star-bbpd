from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from bbp.actor_manifests import actor_manifest_document
from bbp.config import Config


log = logging.getLogger(__name__)


class _ManifestHandler(BaseHTTPRequestHandler):
    server_version = "star-bbpd-manifest/1"

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
        if self.path == "/healthz":
            self._json_response(
                200,
                {
                    "status": "ok",
                    "service": str(self.server.config.service.get("name", "bbp-actors")),
                },
            )
            return
        if self.path == "/api/v1/actors":
            self._json_response(200, actor_manifest_document(self.server.config))
            return
        self._json_response(404, {"status": "error", "message": "not found"})

    def log_message(self, format: str, *args: Any) -> None:
        log.debug("manifest-http: " + format, *args)

    def _json_response(self, status: int, document: dict[str, Any]) -> None:
        body = json.dumps(document, separators=(",", ":"), sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


class _ManifestHttpServer(HTTPServer):
    def __init__(self, server_address: tuple[str, int], config: Config):
        self.config = config
        super().__init__(server_address, _ManifestHandler)


class ActorManifestHttpServer:
    def __init__(self, config: Config):
        host = str(config.service.get("manifest_http_host", "127.0.0.1"))
        port = int(config.service.get("manifest_http_port", 8765))
        self._server = _ManifestHttpServer((host, port), config)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="star-bbpd-manifest-http",
            daemon=True,
        )

    @property
    def host(self) -> str:
        return str(self._server.server_address[0])

    @property
    def port(self) -> int:
        return int(self._server.server_address[1])

    def start(self) -> None:
        self._thread.start()
        log.info("Actor manifest HTTP listening on %s:%s", self.host, self.port)

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


def start_manifest_http(config: Config) -> ActorManifestHttpServer | None:
    if not config.service.get("manifest_http_enabled", False):
        return None
    server = ActorManifestHttpServer(config)
    server.start()
    return server
