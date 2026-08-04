#!/usr/bin/env python3

import re
from typing import Any, Dict, List, Optional, Tuple

from bbp.document_boundary import (
    CanonicalDocument,
    Service,
    TargetDocument,
    build_host,
    build_relation,
    build_service,
)
from bbp.tool_actor import ToolActor


class NmapActor(ToolActor):
    """Parse Nmap output into strict 0.9 host documents."""

    def _build_command(self, target: TargetDocument) -> List[str]:
        bin_path = self.tool_config.get("bin", "nmap")
        args = self.tool_config.get("args", [])
        return [bin_path, *args, target.target]

    def _parse_line(
        self, line: str, target: TargetDocument, context: Dict[str, Any]
    ) -> Optional[Tuple[CanonicalDocument | None, CanonicalDocument | None]]:
        line = line.strip()
        scan_report_match = re.match(
            r"Nmap scan report for (.+?)(?:\s+\((.+?)\))?$", line
        )
        if scan_report_match:
            result = self._emit_current_host(target, context)
            hostname = scan_report_match.group(1)
            ip = scan_report_match.group(2) or hostname
            context["current_host"] = hostname
            context["current_ip"] = ip
            context["current_ports"] = []
            return result

        port_match = re.match(
            r"(\d+)/(tcp|udp)\s+(\w+)\s+(\S+)(?:\s+(.+?))?$", line
        )
        if port_match and "current_host" in context:
            port_num = int(port_match.group(1))
            state = port_match.group(3)
            service_name = port_match.group(4)
            version = port_match.group(5) or ""
            if state == "open":
                service = build_service(port_num, service_name, version.strip())
                context["current_ports"].append(service)
        return None

    def _emit_current_host(
        self, target: TargetDocument, context: Dict[str, Any]
    ) -> Optional[Tuple[CanonicalDocument | None, CanonicalDocument | None]]:
        if "current_host" not in context:
            return None

        hostname = context["current_host"]
        ip = context["current_ip"]
        ports: list[Service] = context.get("current_ports", [])
        host = build_host(hostname, ip, ports, target, "nmap", os_name="")
        relation = build_relation(
            target.id,
            host.id,
            "related-to",
            "host-discovery",
            target,
            "nmap",
        )

        context.pop("current_host", None)
        context.pop("current_ip", None)
        context.pop("current_ports", None)
        return (host, relation)

    def on_receive(self, message):
        return super().on_receive(message)
