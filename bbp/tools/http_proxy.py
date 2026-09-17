#!/usr/bin/env python3

from __future__ import annotations

import json
import logging
import sys
from typing import Any, Dict, List, Optional, Tuple

from bbp.document_boundary import CanonicalDocument, TargetDocument, build_relation
from bbp.network_capture import ProfileDocument, http_transaction_from_record
from bbp.target_options import string_option
from bbp.tool_actor import ToolActor

log = logging.getLogger(__name__)


class HttpProxyActor(ToolActor):
    """Capture one bounded HTTP exchange, optionally through one static operator proxy.

    Proxy credentials are read by the adapter from an operator-owned environment
    variable. Targets can name logical proxy/challenge actor URIs for provenance,
    but cannot inject proxy endpoints or credentials.
    """

    def _build_command(self, target: TargetDocument) -> List[str]:
        method = string_option(target.options, "method", "GET").upper()
        if method not in {"GET", "HEAD"}:
            raise ValueError("http-proxy actor only permits GET and HEAD")
        timeout = float(self.tool_config.get("timeout_sec", 30))
        max_body = int(self.tool_config.get("max_body_bytes", 1048576))
        artifact_dir = str(self.tool_config.get("artifact_dir", "artifacts/http-bodies"))
        artifact_prefix = str(
            self.tool_config.get("artifact_uri_prefix", "artifact://bbp/http-bodies")
        )
        cmd = [
            sys.executable,
            "-m",
            "bbp.http_capture_adapter",
            "--url",
            target.target,
            "--method",
            method,
            "--timeout",
            str(timeout),
            "--max-body-bytes",
            str(max_body),
            "--artifact-dir",
            artifact_dir,
            "--artifact-uri-prefix",
            artifact_prefix,
        ]
        proxy_env = self.tool_config.get("proxy_env")
        if isinstance(proxy_env, str) and proxy_env:
            cmd.extend(["--proxy-env", proxy_env])
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
            record = json.loads(line)
        except json.JSONDecodeError:
            log.debug("[http-proxy] ignoring non-JSON stdout: %s", line[:120])
            return None
        if not isinstance(record, dict):
            return None
        transaction = http_transaction_from_record(target, record)
        relation = build_relation(
            target.id,
            transaction.id,
            "related-to",
            "http-transaction-capture",
            target,
            "http-proxy",
        )
        return transaction, relation
