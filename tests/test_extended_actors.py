from __future__ import annotations

import json
from pathlib import Path

import pytest

from bbp.document_boundary import build_target
from bbp.manifest_catalog import register_extended_manifests
from bbp.manifests import ACTOR_MANIFESTS
from bbp.network_capture import http_transaction_from_record, web_capture_from_record
from bbp.tools.bbot import BbotActor
from bbp.tools.http_proxy import HttpProxyActor
from bbp.tools.nuclei import NucleiActor
from bbp.tools.screenshot import ScreenshotActor

NOW = "2026-09-17T01:00:00Z"


class DummyPublisher:
    def publish_required(self, message, timeout):
        raise AssertionError("publisher should not be used by command construction")

    def tell(self, message):
        return None


def actor_instance(cls, name: str, config: dict):
    return cls(name, DummyPublisher(), config)


def target(actor: str, value: str, options=()):
    return build_target(
        actor,
        value,
        "fixture",
        options=options,
        now=NOW,
    )


def test_extended_manifest_catalog_registers_public_actor_boundaries():
    register_extended_manifests()
    assert {"bbot", "nuclei", "http-proxy", "screenshot"} <= set(ACTOR_MANIFESTS)
    assert ACTOR_MANIFESTS["http-proxy"].output_dtypes == ("http-transaction", "relation")
    assert ACTOR_MANIFESTS["screenshot"].output_dtypes == ("web-capture", "relation")


def test_bbot_command_uses_json_stdout_and_blocks_nuclei_delegation():
    actor = actor_instance(
        BbotActor,
        "bbot-0",
        {"bin": "bbot", "allowed_modules": ["httpx", "sslcert"], "modules": ["httpx"]},
    )
    cmd = actor._build_command(target("bbot", "example.test"))
    assert cmd[:3] == ["bbot", "-t", "example.test"]
    assert "--json" in cmd
    assert "--brief" in cmd
    assert "--no-deps" in cmd

    with pytest.raises(ValueError, match="dedicated actors"):
        actor._build_command(
            target("bbot", "example.test", options=[{"modules": ["nuclei"]}])
        )


def test_nuclei_requires_explicit_authorized_scope_and_omits_raw_oast():
    actor = actor_instance(
        NucleiActor,
        "nuclei-0",
        {
            "bin": "nuclei",
            "allowed_tags": ["tech"],
            "allowed_templates": [],
            "rate_limit": 5,
            "concurrency": 2,
            "bulk_size": 2,
        },
    )
    with pytest.raises(ValueError, match="authorized_scope"):
        actor._build_command(target("nuclei", "https://example.test"))

    cmd = actor._build_command(
        target(
            "nuclei",
            "https://example.test",
            options=[{"authorized_scope": True}, {"tags": ["tech"]}],
        )
    )
    assert "-omit-raw" in cmd
    assert "-no-interactsh" in cmd
    assert "-restrict-local-network-access" in cmd
    assert "dos,fuzz,bruteforce,local" not in cmd  # sorted output is deterministic, not order-coupled
    excluded = cmd[cmd.index("-exclude-tags") + 1].split(",")
    assert {"dos", "fuzz", "bruteforce", "local"} <= set(excluded)


def test_http_proxy_command_does_not_accept_proxy_endpoint_from_target():
    actor = actor_instance(
        HttpProxyActor,
        "http-proxy-0",
        {
            "timeout_sec": 20,
            "max_body_bytes": 4096,
            "proxy_env": "STARINTEL_HTTP_PROXY",
        },
    )
    cmd = actor._build_command(
        target(
            "http-proxy",
            "https://example.test",
            options=[
                {"method": "HEAD"},
                {"proxy_url": "http://attacker.invalid:8080"},
                {"proxy_actor_uri": "star://proxy.starintel.actor/actor/egress"},
            ],
        )
    )
    assert "http://attacker.invalid:8080" not in cmd
    assert "--proxy-env" in cmd
    assert cmd[cmd.index("--method") + 1] == "HEAD"


def test_http_transaction_profile_redacts_headers_and_records_actor_uris():
    tx = http_transaction_from_record(
        target(
            "http-proxy",
            "https://example.test",
            options=[
                {"proxy_actor_uri": "star://proxy.starintel.actor/actor/egress"},
                {"challenge_actor_uri": "star://browser.starintel.actor/actor/challenge"},
            ],
        ),
        {
            "method": "GET",
            "url": "https://example.test",
            "response_status": 403,
            "request_headers": {"Authorization": "secret"},
            "response_headers": {"Set-Cookie": "secret"},
            "response_body_artifact_uri": "artifact://bbp/http-bodies/a.bin",
            "response_body_hash": "sha256:a",
            "challenge_status": "observed",
            "started_at": NOW,
        },
    )
    value = tx.to_dict()
    assert value["dtype"] == "http-transaction"
    assert value["schema_version"] == "0.9.0"
    assert value["extensions"]["starintel.profile"]["release_version"] == "0.9.2"
    assert value["data"]["request_headers"]["Authorization"] == "[REDACTED]"
    assert value["data"]["proxy_actor_uri"].startswith("star://")
    assert value["data"]["challenge_actor_uri"].startswith("star://")


def test_web_capture_requires_screenshot_artifact_reference():
    capture = web_capture_from_record(
        target("screenshot", "https://example.test"),
        {
            "url": "https://example.test",
            "screenshot_uri": "artifact://bbp/screenshots/a.png",
            "screenshot_hash": "sha256:a",
            "captured_at": NOW,
        },
    )
    assert capture.dtype == "web-capture"
    assert json.loads(capture.to_json())["data"]["screenshot_uri"].startswith("artifact://")


def test_screenshot_command_disables_html_and_network_log_capture(tmp_path: Path):
    actor = actor_instance(
        ScreenshotActor,
        "screenshot-0",
        {
            "bin": "gowitness",
            "artifact_dir": str(tmp_path),
            "page_timeout_sec": 20,
            "delay_sec": 0,
        },
    )
    cmd = actor._build_command(target("screenshot", "https://example.test"))
    assert cmd[:4] == ["gowitness", "-q", "scan", "single"]
    assert "--skip-html" in cmd
    assert "--skip-network-logs" in cmd
    assert "--write-jsonl" in cmd
    assert cmd[cmd.index("--write-jsonl-file") + 1] == "/dev/stdout"
