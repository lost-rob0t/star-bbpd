#!/usr/bin/env python3

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from bbp.tool_actor import ToolActor
from bbp.util import propagate_sources, propagate_dataset

# Import starintel_doc (will be added as dependency)
import sys
sys.path.insert(0, "/home/unseen/Documents/Projects/starintelV4/spec/starintel_doc")
from starintel_doc.hosts import Url
from starintel_doc.relations import Relation

log = logging.getLogger(__name__)


class KatanaActor(ToolActor):
    """
    Katana tool actor.

    Consumes URL targets, runs katana crawler, emits URL docs + relations.

    Command: katana -silent -json -u <url>
    Output: JSON lines, one JSON object per discovered URL
    """

    def _build_command(self, target: Any) -> List[str]:
        """
        Build katana command.

        Args:
            target: StarIntel Target object with .target field (URL or normalized to URL)

        Returns:
            Command list: ["katana", "-silent", "-json", "-u", "https://example.com", ...]
        """
        bin_path = self.tool_config.get("bin", "katana")
        args = self.tool_config.get("args", [])

        # Normalize target to URL if needed (add https:// if missing)
        target_url = target.target
        if not target_url.startswith(("http://", "https://")):
            target_url = f"https://{target_url}"

        cmd = [bin_path]

        # Add configured args if not already containing -u flag
        if "-u" not in args:
            cmd.extend(args)
            cmd.extend(["-u", target_url])
        else:
            # Args already contain -u, use as-is (replace target)
            cmd.extend([arg if arg != "{target}" else target_url for arg in args])

        return cmd

    def _parse_line(
        self, line: str, target: Any, context: Dict[str, Any]
    ) -> Optional[Tuple[Any, Any]]:
        """
        Parse a line of katana output.

        Args:
            line: Line from katana stdout (JSON object)
            target: StarIntel Target object
            context: Unused for katana (stateless)

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

            # Extract URL from JSON (katana uses "url" or "endpoint" field)
            url_str = data.get("url") or data.get("endpoint")
            if not url_str:
                log.warning(f"[katana] No 'url' or 'endpoint' field in JSON: {line[:100]}")
                return None

            # Create URL document
            url_doc = Url(
                url=url_str,
                path=data.get("path", ""),
                query=data.get("query", ""),
                content="",  # TODO should we capture this?
                dataset=propagate_dataset(target),
                sources=propagate_sources(target, "katana"),
            )
            url_doc.set_id()  # Generates hash-based ID

            # Create Relation: seed-url -> discovered-url
            # Source is the target (seed URL), target is discovered URL
            relation = Relation(
                source=target.id,
                target=url_doc.id,
                predicate="links-to",
                note="crawl",
                dataset=propagate_dataset(target),
                sources=propagate_sources(target, "katana"),
            )
            relation.set_id()  # Generates ULID

            return (url_doc, relation)

        except json.JSONDecodeError as e:
            log.warning(f"[katana] Failed to parse JSON: {e}, line: {line[:100]}")
            return None
        except Exception as e:
            log.error(f"[katana] Error parsing line: {e}", exc_info=True)
            return None
