#!/usr/bin/env python3

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from bbp.tool_actor import ToolActor
from bbp.util import propagate_sources, propagate_dataset

# Import starintel_doc (will be added as dependency)
import sys
sys.path.insert(0, "/home/unseen/Documents/Projects/starintelV4/spec/starintel_doc")
from starintel_doc.hosts import Host, Service
from starintel_doc.relations import Relation

log = logging.getLogger(__name__)


class NmapActor(ToolActor):
    """
    Nmap tool actor.

    Consumes host/IP/CIDR targets, runs nmap, emits Host docs + relations.

    Command: nmap -sV <target>
    Output: Plain text nmap output (stateful parsing required)

    Parse strategy:
    - "Nmap scan report for <host> (<ip>)" -> start new host
    - "<port>/<protocol> <state> <service> <version>" -> add port to current host
    - Emit host doc when next host starts or scan finishes
    """

    def _build_command(self, target: Any) -> List[str]:
        """
        Build nmap command.

        Args:
            target: StarIntel Target object with .target field (host/IP/CIDR)

        Returns:
            Command list: ["nmap", "-sV", "192.168.1.1", ...]
        """
        bin_path = self.tool_config.get("bin", "nmap")
        args = self.tool_config.get("args", [])

        cmd = [bin_path]
        cmd.extend(args)
        cmd.append(target.target)

        return cmd

    def _parse_line(
        self, line: str, target: Any, context: Dict[str, Any]
    ) -> Optional[Tuple[Any, Any]]:
        """
        Parse a line of nmap output (stateful).

        Args:
            line: Line from nmap stdout
            target: StarIntel Target object
            context: Shared context dict for accumulating host state
                     Keys: current_host, current_ip, current_ports

        Returns:
            (Host doc, Relation) tuple when host is complete, or None if line is partial
        """
        line = line.strip()

        # Pattern: "Nmap scan report for <hostname> (<ip>)"
        # or: "Nmap scan report for <ip>"
        scan_report_match = re.match(
            r"Nmap scan report for (.+?)(?:\s+\((.+?)\))?$", line
        )
        if scan_report_match:
            # Emit previous host if exists
            result = self._emit_current_host(target, context)

            # Start new host
            hostname = scan_report_match.group(1)
            ip = scan_report_match.group(2) or hostname  # Use hostname as IP if no IP in parens

            context["current_host"] = hostname
            context["current_ip"] = ip
            context["current_ports"] = []

            return result

        # Pattern: "<port>/<proto> <state> <service> <version>"
        # Example: "80/tcp open http nginx 1.14.0"
        port_match = re.match(
            r"(\d+)/(tcp|udp)\s+(\w+)\s+(\S+)(?:\s+(.+?))?$", line
        )
        if port_match and "current_host" in context:
            port_num = int(port_match.group(1))
            protocol = port_match.group(2)
            state = port_match.group(3)
            service_name = port_match.group(4)
            version = port_match.group(5) or ""

            # Only add if port is open
            if state == "open":
                service = Service(
                    port=port_num,
                    name=service_name,
                    version=version.strip(),
                )
                context["current_ports"].append(service)

        # Other lines ignored (host status, latency, etc.)
        return None

    def _emit_current_host(
        self, target: Any, context: Dict[str, Any]
    ) -> Optional[Tuple[Any, Any]]:
        """
        Emit the current host from context.

        Args:
            target: StarIntel Target object
            context: Shared context dict

        Returns:
            (Host doc, Relation) tuple if host exists, None otherwise
        """
        if "current_host" not in context:
            return None

        hostname = context["current_host"]
        ip = context["current_ip"]
        ports = context.get("current_ports", [])

        # Create Host document
        host = Host(
            hostname=hostname,
            ip=ip,
            ports=ports,
            os="",  # Could be enhanced with OS detection
            dataset=propagate_dataset(target),
            sources=propagate_sources(target, "nmap"),
        )
        host.set_id()  # Generates hash-based ID from IP

        # Create Relation: target -> host
        relation = Relation(
            source=target.id,
            target=host.id,
            predicate="related-to",
            note="host-discovery",
            dataset=propagate_dataset(target),
            sources=propagate_sources(target, "nmap"),
        )
        relation.set_id()  # Generates ULID

        # Clear current host from context
        context.pop("current_host", None)
        context.pop("current_ip", None)
        context.pop("current_ports", None)

        return (host, relation)

    def on_receive(self, message):
        """
        Override to emit final host after scan completes.

        We need to emit the last host after processing all lines.
        """
        from bbp.tool_actor import ScanTarget

        if isinstance(message, ScanTarget):
            # Let parent handle the scan
            result = super().on_receive(message)

            # After scan completes, emit any remaining host in context
            # This is tricky because we need access to context...
            # Actually, the base class doesn't expose context after scan.
            # We need a different approach.

            return result
        else:
            return super().on_receive(message)
