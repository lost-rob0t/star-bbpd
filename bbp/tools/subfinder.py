#!/usr/bin/env python3

import logging
from typing import Any, Dict, List, Optional, Tuple

from bbp.document_boundary import (
    CanonicalDocument,
    TargetDocument,
    build_domain,
    build_relation,
)
from bbp.tool_actor import ToolActor

log = logging.getLogger(__name__)


class SubfinderActor(ToolActor):
    """Discover subdomains and emit strict 0.9 domains and relations."""

    def _build_command(self, target: TargetDocument) -> List[str]:
        bin_path = self.tool_config.get("bin", "subfinder")
        args = self.tool_config.get("args", [])
        cmd = [bin_path]
        if "-d" not in args:
            cmd.extend(args)
            cmd.extend(["-d", target.target])
        else:
            cmd.extend([arg if arg != "{target}" else target.target for arg in args])
        return cmd

    def _make_domain_doc(
        self, domain_str: str, target: TargetDocument
    ) -> CanonicalDocument:
        return build_domain(domain_str, target, "subfinder")

    def _pre_scan(self, target: TargetDocument, context: Dict[str, Any]) -> Tuple[int, int]:
        root_str = target.target.strip()
        if not root_str:
            return (0, 0)

        root_domain = self._make_domain_doc(root_str, target)
        context["root_domain"] = root_domain
        context["seen_domains"] = {root_str}
        self._emit_document(root_domain)
        log.info(
            "[%s] Emitted root domain for target: %s (%s)",
            self.actor_name,
            root_str,
            root_domain.id,
        )
        return (1, 0)

    def _parse_line(
        self, line: str, target: TargetDocument, context: Dict[str, Any]
    ) -> Optional[Tuple[CanonicalDocument | None, CanonicalDocument | None]]:
        domain_str = line.strip()
        if not domain_str:
            return None

        root_domain = context.get("root_domain")
        if root_domain is None:
            root_str = target.target.strip()
            if not root_str:
                return None
            root_domain = self._make_domain_doc(root_str, target)
            context["root_domain"] = root_domain
            context["seen_domains"] = {root_str}
            self._emit_document(root_domain)

        seen = context.setdefault("seen_domains", set())
        if domain_str in seen:
            return None
        seen.add(domain_str)

        subdomain = self._make_domain_doc(domain_str, target)
        relation = build_relation(
            root_domain.id,
            subdomain.id,
            "subdomain",
            "subdomain",
            target,
            "subfinder",
        )
        return (subdomain, relation)
