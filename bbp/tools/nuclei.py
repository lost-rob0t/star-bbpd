#!/usr/bin/env python3

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from bbp.document_boundary import CanonicalDocument, TargetDocument, build_relation
from bbp.observations import build_observation
from bbp.target_options import bool_option, string_list_option
from bbp.tool_actor import ToolActor

log = logging.getLogger(__name__)

DEFAULT_EXCLUDED_TAGS = ("dos", "fuzz", "bruteforce", "local")
DEFAULT_EXCLUDED_TYPES = ("headless", "code", "javascript")


class NucleiActor(ToolActor):
    """Detection-oriented Nuclei actor with explicit scope authorization.

    It intentionally omits raw request/response material and OAST/headless/code
    execution by default. Intrusive behavior must not be enabled through target
    options.
    """

    def _build_command(self, target: TargetDocument) -> List[str]:
        if not bool_option(target.options, "authorized_scope", False):
            raise ValueError("nuclei target requires authorized_scope=true")

        bin_path = self.tool_config.get("bin", "nuclei")
        rate_limit = int(self.tool_config.get("rate_limit", 10))
        concurrency = int(self.tool_config.get("concurrency", 5))
        bulk_size = int(self.tool_config.get("bulk_size", 5))
        if min(rate_limit, concurrency, bulk_size) <= 0:
            raise ValueError("nuclei rate_limit, concurrency and bulk_size must be positive")

        excluded_tags = list(DEFAULT_EXCLUDED_TAGS)
        configured_tags = self.tool_config.get("exclude_tags", [])
        if isinstance(configured_tags, list):
            excluded_tags.extend(item for item in configured_tags if isinstance(item, str))

        cmd = [
            bin_path,
            "-u",
            target.target,
            "-jsonl",
            "-silent",
            "-no-color",
            "-omit-raw",
            "-no-interactsh",
            "-restrict-local-network-access",
            "-rate-limit",
            str(rate_limit),
            "-concurrency",
            str(concurrency),
            "-bulk-size",
            str(bulk_size),
            "-exclude-tags",
            ",".join(sorted(set(excluded_tags))),
            "-exclude-type",
            ",".join(DEFAULT_EXCLUDED_TYPES),
        ]

        allowed_tags = set(self.tool_config.get("allowed_tags", []))
        requested_tags = string_list_option(target.options, "tags")
        if requested_tags:
            denied = sorted(set(requested_tags).difference(allowed_tags))
            if denied:
                raise ValueError(f"nuclei tag(s) are not allowlisted: {', '.join(denied)}")
            cmd.extend(["-tags", ",".join(requested_tags)])

        templates = string_list_option(target.options, "templates")
        if templates:
            allowed_templates = set(self.tool_config.get("allowed_templates", []))
            denied = sorted(set(templates).difference(allowed_templates))
            if denied:
                raise ValueError(
                    f"nuclei template(s) are not allowlisted: {', '.join(denied)}"
                )
            for template in templates:
                cmd.extend(["-templates", template])

        return cmd

    def _parse_line(
        self,
        line: str,
        target: TargetDocument,
        context: Dict[str, Any],
    ) -> Optional[Tuple[CanonicalDocument | None, CanonicalDocument | None]]:
        del context
        line = line.strip()
        if not line:
            return None
        try:
            result = json.loads(line)
        except json.JSONDecodeError:
            log.debug("[nuclei] ignoring non-JSON stdout: %s", line[:120])
            return None
        if not isinstance(result, dict):
            return None

        info = result.get("info") if isinstance(result.get("info"), dict) else {}
        template_id = str(result.get("template-id") or result.get("template_id") or "unknown")
        matched_at = str(result.get("matched-at") or result.get("matched_at") or target.target)
        value = {
            "template_id": template_id,
            "name": info.get("name", ""),
            "severity": info.get("severity", "unknown"),
            "tags": info.get("tags", []),
            "matcher_name": result.get("matcher-name", result.get("matcher_name", "")),
            "matched_at": matched_at,
            "host": result.get("host", ""),
            "type": result.get("type", ""),
            "timestamp": result.get("timestamp"),
        }
        observation = build_observation(
            target,
            actor="nuclei",
            observation_type="nuclei-template-match",
            value=value,
            method="template-detection",
            instrument=template_id,
            extensions={
                "starintel.nuclei.v1": {
                    "raw_omitted": True,
                    "oast_disabled": True,
                    "active_bypass_disabled": True,
                }
            },
        )
        relation = build_relation(
            target.id,
            observation.id,
            "related-to",
            f"nuclei:{template_id}",
            target,
            "nuclei",
        )
        return observation, relation
