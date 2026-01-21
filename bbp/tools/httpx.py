#!/usr/bin/env python3

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from bbp.tool_actor import ToolActor
from bbp.util import propagate_sources, propagate_dataset

# Import starintel_doc
from starintel_doc.hosts import Url
from starintel_doc.relations import Relation

log = logging.getLogger(__name__)


class HttpxActor(ToolActor):
    """
    Httpx tool actor.

    Consumes host/domain/url targets, runs httpx, emits URL docs + relations.

    Command: httpx -silent -json -u <target>
    Output: JSON lines, one JSON object per request
    """

    def _build_command(self, target: Any) -> List[str]:
        """
        Build httpx command.

        Args:
            target: StarIntel Target object with .target field (host/domain/url)

        Returns:
            Command list: ["httpx", "-silent", "-json", "-u", "example.com", ...]
        """
        bin_path = self.tool_config.get("bin", "httpx")
        args = self.tool_config.get("args", [])

        cmd = [bin_path]

        # Add configured args if not already containing -u flag
        if "-u" not in args:
            cmd.extend(args)
            cmd.extend(["-u", target.target])
        else:
            # Args already contain -u, use as-is (replace target)
            cmd.extend([arg if arg != "{target}" else target.target for arg in args])

        return cmd

    def _parse_line(
        self, line: str, target: Any, context: Dict[str, Any]
    ) -> Optional[Tuple[Any, Any]]:
        """
        Parse a line of httpx output.

        Args:
            line: Line from httpx stdout (JSON object)
            target: StarIntel Target object
            context: Unused for httpx (stateless)

        Returns:
            (Url doc, Relation) tuple, or None if parse error
        """
        # Strip whitespace
        line = line.strip()

        # Skip empty lines
        if not line:
            return None

        try:
            # Parse JSON
            data = json.loads(line)

            # Extract URL from JSON
            url_str = data.get("url")
            if not url_str:
                log.warning(f"[httpx] No 'url' field in JSON: {line[:100]}")
                return None

            # Extract additional fields if available
            status_code = data.get("status_code", 0)
            content_length = data.get("content_length", 0)
            title = data.get("title", "")

            # Create URL document
            url_doc = Url(
                url=url_str,
                path=data.get("path", ""),
                query=data.get("query", ""),
                content="",  # httpx doesn't return body by default
                dataset=propagate_dataset(target),
                sources=propagate_sources(target, "httpx"),
            )
            url_doc.set_id()  # Generates hash-based ID

            # Create Relation: target -> url
            relation = Relation(
                source=target.id,
                target=url_doc.id,
                predicate="related-to",
                note="httpx",
                dataset=propagate_dataset(target),
                sources=propagate_sources(target, "httpx"),
            )
            relation.set_id()  # Generates ULID

            return (url_doc, relation)

        except json.JSONDecodeError as e:
            log.warning(f"[httpx] Failed to parse JSON: {e}, line: {line[:100]}")
            return None
        except Exception as e:
            log.error(f"[httpx] Error parsing line: {e}", exc_info=True)
            return None
