from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping

from starintel_doc import PROFILE_VERSION, build_http_transaction, build_web_capture

from bbp.document_boundary import TargetDocument
from bbp.target_options import string_option

EXPECTED_PROFILE_VERSION = "0.9.2"
NETWORK_CAPTURE_DOC_COMMIT = "2e52d38c807cccbe5e9b931e645bb46c4491e497"

if PROFILE_VERSION != EXPECTED_PROFILE_VERSION:
    raise RuntimeError(
        f"starintel_doc network-capture profile {PROFILE_VERSION!r}; "
        f"expected {EXPECTED_PROFILE_VERSION!r}"
    )


@dataclass(frozen=True, slots=True)
class ProfileDocument:
    value: dict[str, Any]

    @property
    def id(self) -> str:
        return str(self.value["_id"])

    @property
    def dtype(self) -> str:
        return str(self.value["dtype"])

    @property
    def source(self) -> str:
        data = self.value.get("data", {})
        return str(data.get("url", self.id))

    def to_dict(self) -> dict[str, Any]:
        return deepcopy(self.value)

    def to_json(self, *, pretty: bool = False) -> str:
        return json.dumps(
            self.value,
            sort_keys=True,
            indent=2 if pretty else None,
            separators=None if pretty else (",", ":"),
        )


def _copy_capture_context(record: Mapping[str, Any], fields: dict[str, Any]) -> None:
    for key in (
        "challenge_status",
        "captcha_detection_id",
        "captcha_capability",
        "browser_session_ref",
        "network_context_ref",
    ):
        if key in record and record[key] is not None:
            fields[key] = deepcopy(record[key])


def _copy_proxy_binding(target: TargetDocument, fields: dict[str, Any]) -> None:
    proxy_actor_uri = string_option(target.options, "proxy_actor_uri")
    if proxy_actor_uri:
        fields["proxy_actor_uri"] = proxy_actor_uri


def http_transaction_from_record(
    target: TargetDocument,
    record: Mapping[str, Any],
) -> ProfileDocument:
    method = str(record.get("method") or "GET")
    url = str(record.get("url") or target.target)
    status = record.get("response_status", record.get("status_code", 0))
    if not isinstance(status, int) or isinstance(status, bool):
        status = 0
    request_headers = record.get("request_headers")
    response_headers = record.get("response_headers", record.get("headers"))
    fields: dict[str, Any] = {}
    passthrough = (
        "request_id",
        "connection_id",
        "parent_transaction_id",
        "scheme",
        "host",
        "port",
        "path",
        "query",
        "http_version",
        "request_body_size",
        "request_body_hash",
        "request_body_artifact_uri",
        "response_reason",
        "response_body_size",
        "response_body_hash",
        "response_body_artifact_uri",
        "started_at",
        "ended_at",
        "duration_ms",
        "remote_ip",
        "remote_port",
        "tls_version",
        "tls_cipher",
        "tls_server_name",
        "certificate_sha256",
        "redirect_from_id",
        "redirect_to_id",
        "request_truncated",
        "response_truncated",
    )
    for key in passthrough:
        if key in record and record[key] is not None:
            fields[key] = deepcopy(record[key])
    _copy_capture_context(record, fields)
    fields["capture_actor_uri"] = string_option(
        target.options,
        "capture_actor_uri",
        "star://bbp.starintel.actor/actor/http-proxy",
    )
    _copy_proxy_binding(target, fields)

    document = build_http_transaction(
        dataset=target.dataset,
        method=method,
        url=url,
        response_status=status,
        transaction_id=(
            str(record["transaction_id"])
            if isinstance(record.get("transaction_id"), str)
            else None
        ),
        request_headers=(request_headers if isinstance(request_headers, Mapping) else {}),
        response_headers=(response_headers if isinstance(response_headers, Mapping) else {}),
        observed_at=(
            str(record["started_at"])
            if isinstance(record.get("started_at"), str)
            else None
        ),
        fields=fields,
    )
    return ProfileDocument(document)


def web_capture_from_record(
    target: TargetDocument,
    record: Mapping[str, Any],
) -> ProfileDocument:
    url = str(record.get("url") or target.target)
    screenshot_uri = str(record.get("screenshot_uri") or "")
    screenshot_hash = str(record.get("screenshot_hash") or "")
    if not screenshot_uri or not screenshot_hash:
        raise ValueError("screenshot record requires screenshot_uri and screenshot_hash")

    fields: dict[str, Any] = {}
    for key in (
        "final_url",
        "title",
        "status_code",
        "browser",
        "browser_version",
        "viewport_width",
        "viewport_height",
        "device_scale_factor",
        "screenshot_media_type",
        "screenshot_size_bytes",
        "dom_artifact_uri",
        "dom_artifact_hash",
        "dom_artifact_size_bytes",
        "captured_at",
        "http_transaction_ids",
    ):
        if key in record and record[key] is not None:
            fields[key] = deepcopy(record[key])
    _copy_capture_context(record, fields)
    fields["capture_actor_uri"] = string_option(
        target.options,
        "capture_actor_uri",
        "star://bbp.starintel.actor/actor/screenshot",
    )
    _copy_proxy_binding(target, fields)

    document = build_web_capture(
        dataset=target.dataset,
        url=url,
        screenshot_uri=screenshot_uri,
        screenshot_hash=screenshot_hash,
        capture_id=(str(record["capture_id"]) if isinstance(record.get("capture_id"), str) else None),
        captured_at=(str(record["captured_at"]) if isinstance(record.get("captured_at"), str) else None),
        fields=fields,
    )
    return ProfileDocument(document)
