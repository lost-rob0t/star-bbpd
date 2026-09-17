#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from bbp.document_boundary import CanonicalDocument, TargetDocument, build_relation
from bbp.network_capture import ProfileDocument, web_capture_from_record
from bbp.tool_actor import ToolActor

log = logging.getLogger(__name__)

CHALLENGE_MARKERS = (
    "captcha",
    "verify you are human",
    "attention required",
    "cloudflare",
    "access denied",
)


class ScreenshotActor(ToolActor):
    """Capture one URL with gowitness and emit an artifact-backed web-capture."""

    def _build_command(self, target: TargetDocument) -> List[str]:
        bin_path = self.tool_config.get("bin", "gowitness")
        artifact_dir = Path(self.tool_config.get("artifact_dir", "artifacts/screenshots"))
        artifact_dir.mkdir(parents=True, exist_ok=True)
        timeout = int(self.tool_config.get("page_timeout_sec", 30))
        delay = int(self.tool_config.get("delay_sec", 1))
        if timeout <= 0 or delay < 0:
            raise ValueError("screenshot timeout must be positive and delay non-negative")

        cmd = [
            bin_path,
            "-q",
            "scan",
            "single",
            "--url",
            target.target,
            "--screenshot-path",
            str(artifact_dir),
            "--screenshot-format",
            "png",
            "--timeout",
            str(timeout),
            "--delay",
            str(delay),
            "--skip-html",
            "--skip-network-logs",
            "--write-jsonl",
            "--write-jsonl-file",
            "/dev/stdout",
        ]
        proxy_url = self.tool_config.get("proxy_url")
        if isinstance(proxy_url, str) and proxy_url:
            cmd.extend(["--chrome-proxy", proxy_url])
        return cmd

    def _parse_line(
        self,
        line: str,
        target: TargetDocument,
        context: Dict[str, Any],
    ) -> Optional[Tuple[ProfileDocument | None, CanonicalDocument | None]]:
        del context
        line = line.strip()
        if not line:
            return None
        try:
            result = json.loads(line)
        except json.JSONDecodeError:
            log.debug("[screenshot] ignoring non-JSON stdout: %s", line[:120])
            return None
        if not isinstance(result, dict):
            return None

        filename = self._string_field(result, "file_name", "filename", "Filename")
        if not filename:
            log.warning("[screenshot] result has no screenshot filename")
            return None
        artifact_dir = Path(self.tool_config.get("artifact_dir", "artifacts/screenshots"))
        screenshot_path = artifact_dir / Path(filename).name
        if not screenshot_path.is_file():
            log.warning("[screenshot] screenshot artifact missing: %s", screenshot_path)
            return None

        digest = hashlib.sha256(screenshot_path.read_bytes()).hexdigest()
        artifact_prefix = str(
            self.tool_config.get("artifact_uri_prefix", "artifact://bbp/screenshots")
        ).rstrip("/")
        title = self._string_field(result, "title", "Title")
        status = self._int_field(result, "response_code", "status_code", "ResponseCode")
        final_url = self._string_field(result, "final_url", "FinalURL")
        captured_at = self._string_field(result, "probed_at", "created_at", "CreatedAt")
        challenge_status = "observed" if self._challenge_observed(status, title) else "none"

        record = {
            "url": self._string_field(result, "url", "URL") or target.target,
            "final_url": final_url,
            "title": title,
            "status_code": status,
            "browser": "chromium",
            "screenshot_uri": f"{artifact_prefix}/{screenshot_path.name}",
            "screenshot_hash": f"sha256:{digest}",
            "screenshot_media_type": "image/png",
            "screenshot_size_bytes": screenshot_path.stat().st_size,
            "captured_at": captured_at or None,
            "challenge_status": challenge_status,
        }
        capture = web_capture_from_record(target, record)
        relation = build_relation(
            target.id,
            capture.id,
            "related-to",
            "web-screenshot",
            target,
            "screenshot",
        )
        return capture, relation

    @staticmethod
    def _string_field(value: Dict[str, Any], *names: str) -> str:
        for name in names:
            item = value.get(name)
            if isinstance(item, str):
                return item
        return ""

    @staticmethod
    def _int_field(value: Dict[str, Any], *names: str) -> int:
        for name in names:
            item = value.get(name)
            if isinstance(item, int) and not isinstance(item, bool):
                return item
        return 0

    @staticmethod
    def _challenge_observed(status: int, title: str) -> bool:
        if status in {401, 403, 407, 429, 503}:
            return True
        lowered = title.casefold()
        return any(marker in lowered for marker in CHALLENGE_MARKERS)
