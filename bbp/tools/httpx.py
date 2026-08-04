#!/usr/bin/env python3

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from bbp.document_boundary import (
    CanonicalDocument,
    TargetDocument,
    build_relation,
    build_url,
)
from bbp.tool_actor import ToolActor

log = logging.getLogger(__name__)


class HttpxActor(ToolActor):
    """Parse Httpx JSONL into strict 0.9 URL documents."""

    def _build_command(self, target: TargetDocument) -> List[str]:
        bin_path = self.tool_config.get("bin", "httpx")
        args = self.tool_config.get("args", [])
        cmd = [bin_path]
        if "-u" not in args:
            cmd.extend(args)
            cmd.extend(["-u", target.target])
        else:
            cmd.extend([arg if arg != "{target}" else target.target for arg in args])
        return cmd

    def _parse_line(
        self, line: str, target: TargetDocument, context: Dict[str, Any]
    ) -> Optional[Tuple[CanonicalDocument | None, CanonicalDocument | None]]:
        del context
        line = line.strip()
        if not line:
            return None

        try:
            data = json.loads(line)
            url_str = data.get("url")
            if not url_str:
                log.warning("[httpx] No 'url' field in JSON: %s", line[:100])
                return None

            url_doc = build_url(
                url_str,
                target,
                "httpx",
                path=data.get("path", ""),
                query=data.get("query", ""),
                content="",
            )
            relation = build_relation(
                target.id,
                url_doc.id,
                "related-to",
                "httpx",
                target,
                "httpx",
            )
            return (url_doc, relation)
        except json.JSONDecodeError as exc:
            log.warning("[httpx] Failed to parse JSON: %s, line: %s", exc, line[:100])
            return None
        except Exception as exc:
            log.error("[httpx] Error parsing line: %s", exc, exc_info=True)
            return None
