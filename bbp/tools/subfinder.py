#!/usr/bin/env python3

import logging
from typing import Any, Dict, List, Optional, Tuple

from bbp.tool_actor import ToolActor
from bbp.util import propagate_sources, propagate_dataset

# Import starintel_doc
from starintel_doc.hosts import Domain
from starintel_doc.relations import Relation

log = logging.getLogger(__name__)


class SubfinderActor(ToolActor):
    """
    Subfinder tool actor.

    Consumes domain targets, runs subfinder, emits Domain docs + relations.

    Command: subfinder -silent -d <domain>
    Output: Plain text, one subdomain per line
    """

    def _build_command(self, target: Any) -> List[str]:
        """
        Build subfinder command.

        Args:
            target: StarIntel Target object with .target field (domain)

        Returns:
            Command list: ["subfinder", "-silent", "-d", "example.com", ...]
        """
        bin_path = self.tool_config.get("bin", "subfinder")
        args = self.tool_config.get("args", [])

        cmd = [bin_path]

        # Add configured args if not already containing -d flag
        if "-d" not in args:
            cmd.extend(args)
            cmd.extend(["-d", target.target])
        else:
            # Args already contain -d, use as-is (replace domain)
            cmd.extend([arg if arg != "{target}" else target.target for arg in args])

        return cmd

    def _parse_line(
        self, line: str, target: Any, context: Dict[str, Any]
    ) -> Optional[Tuple[Any, Any]]:
        """
        Parse a line of subfinder output.

        Args:
            line: Line from subfinder stdout (plain text domain)
            target: StarIntel Target object
            context: Unused for subfinder (stateless)

        Returns:
            (Domain doc, Relation) tuple, or None if line is empty
        """
        # Strip whitespace
        domain_str = line.strip()

        # Skip empty lines
        if not domain_str:
            return None

        # Create Domain document
        domain = Domain(
            record_type="A",  # Default to A record (could be enhanced with DNS lookup)
            record=domain_str,
            resolved_addresses=[],  # Could be populated with DNS resolution
            dataset=propagate_dataset(target),
            sources=propagate_sources(target, "subfinder"),
        )
        domain.set_id()  # Generates hash-based ID

        # Create Relation: target -> domain
        relation = Relation(
            source=target.id,
            target=domain.id,
            predicate="has-a",
            note="subdomain",
            dataset=propagate_dataset(target),
            sources=propagate_sources(target, "subfinder"),
        )
        relation.set_id()  # Generates ULID

        return (domain, relation)
