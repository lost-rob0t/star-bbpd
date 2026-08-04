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


class KatanaActor(ToolActor):
    """Parse Katana JSONL into strict 0.9 URL documents."""

    def _build_command(self, target: TargetDocument) -> List[str]:
        bin_path = self.tool_config.get("bin", "katana")
        args = self.tool_config.get("args", [])
        target_url = target.target
        if not target_url.startswith(("http://", "https://")):
            target_url = f"https://{target_url}"

        cmd = [bin_path]
        if "-u" not in args:
            cmd.extend(args)
            cmd.extend(["-u", target_url])
        else:
            cmd.extend([arg if arg != "{target}" else target_url for arg in args])
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
            url_str = data.get("url") or data.get("endpoint")
            if not url_str:
                log.warning("[katana] No URL field in JSON: %s", line[:100])
                return None

            url_doc = build_url(
                url_str,
                target,
                "katana",
                path=data.get("path", ""),
                query=data.get("query", ""),
                content="",
            )
            relation = build_relation(
                target.id,
                url_doc.id,
                "links-to",
                "crawl",
                target,
                "katana",
            )
            return (url_doc, relation)
        except json.JSONDecodeError as exc:
            log.warning("[katana] Failed to parse JSON: %s, line: %s", exc, line[:100])
            return None
        except Exception as exc:
            log.error("[katana] Error parsing line: %s", exc, exc_info=True)
            return None
