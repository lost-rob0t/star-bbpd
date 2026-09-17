#!/usr/bin/env python3

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from bbp.document_boundary import (
    CanonicalDocument,
    TargetDocument,
    build_domain,
    build_host,
    build_relation,
    build_url,
)
from bbp.observations import build_observation
from bbp.target_options import string_list_option
from bbp.tool_actor import ToolActor

log = logging.getLogger(__name__)

SAFE_DEFAULT_MODULES = ("httpx",)
BLOCKED_MODULES = frozenset(
    {
        "nuclei",
        "masscan",
        "ffuf",
        "wpscan",
        "sqlmap",
    }
)


class BbotActor(ToolActor):
    """Run a bounded BBOT scan and normalize selected JSON events.

    BBOT remains the scanner/orchestrator. StarIntel owns scope, identity,
    persistence and publication semantics.
    """

    def _build_command(self, target: TargetDocument) -> List[str]:
        bin_path = self.tool_config.get("bin", "bbot")
        configured = self.tool_config.get("modules", list(SAFE_DEFAULT_MODULES))
        requested = string_list_option(target.options, "modules")
        modules = requested or configured
        if not isinstance(modules, list) or not all(isinstance(item, str) for item in modules):
            raise ValueError("BBOT modules must be a list of strings")
        blocked = sorted(set(modules).intersection(BLOCKED_MODULES))
        if blocked:
            raise ValueError(
                "BBOT actor does not delegate intrusive modules; use their dedicated actors: "
                + ", ".join(blocked)
            )
        allowed = set(self.tool_config.get("allowed_modules", list(SAFE_DEFAULT_MODULES)))
        unknown = sorted(set(modules).difference(allowed))
        if unknown:
            raise ValueError(f"BBOT module(s) are not allowlisted: {', '.join(unknown)}")

        cmd = [
            bin_path,
            "-t",
            target.target,
            "--json",
            "--no-color",
            "--silent",
            "--yes",
            "--no-deps",
            "-eom",
            "csv",
            "txt",
            "json",
        ]
        if modules:
            cmd.extend(["-m", *modules])
        extra = self.tool_config.get("args", [])
        if isinstance(extra, list) and all(isinstance(item, str) for item in extra):
            cmd.extend(extra)
        return cmd

    def _parse_line(
        self,
        line: str,
        target: TargetDocument,
        context: Dict[str, Any],
    ) -> Optional[Tuple[CanonicalDocument | None, CanonicalDocument | None]]:
        line = line.strip()
        if not line:
            return None
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            log.debug("[bbot] ignoring non-JSON stdout: %s", line[:120])
            return None
        if not isinstance(event, dict):
            return None

        event_type = str(event.get("type") or event.get("event_type") or "").upper()
        data = event.get("data_json", event.get("data"))
        module = str(event.get("module") or "bbot")

        if event_type in {"DNS_NAME", "DNS_NAME_UNRESOLVED"} and isinstance(data, str):
            doc = build_domain(data, target, "bbot")
            return doc, build_relation(target.id, doc.id, "related-to", f"bbot:{module}", target, "bbot")

        if event_type in {"URL", "URL_UNVERIFIED"} and isinstance(data, str):
            doc = build_url(data, target, "bbot")
            return doc, build_relation(target.id, doc.id, "related-to", f"bbot:{module}", target, "bbot")

        if event_type == "IP_ADDRESS" and isinstance(data, str):
            doc = build_host("", data, [], target, "bbot")
            return doc, build_relation(target.id, doc.id, "related-to", f"bbot:{module}", target, "bbot")

        if event_type in {"FINDING", "VULNERABILITY", "HTTP_RESPONSE"}:
            observation = build_observation(
                target,
                actor="bbot",
                observation_type=f"bbot-{event_type.lower().replace('_', '-')}",
                value=data,
                method="bbot-event",
                instrument=module,
                extensions={
                    "starintel.bbot.v1": {
                        "scope_distance": event.get("scope_distance"),
                        "tags": event.get("tags", []),
                    }
                },
            )
            return observation, build_relation(
                target.id,
                observation.id,
                "related-to",
                f"bbot:{module}",
                target,
                "bbot",
            )

        return None
