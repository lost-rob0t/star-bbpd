#!/usr/bin/env python3

import logging
from typing import Any, Dict, List, Optional, Tuple

from bbp.determinism import normalize_domain_name
from bbp.document_boundary import (
    CanonicalDocument,
    TargetDocument,
    build_domain,
    build_relation,
)
from bbp.tool_actor import ToolActor

log = logging.getLogger(__name__)


class SubfinderActor(ToolActor):
    """Discover child domains and emit strict 0.9 domains and relations."""

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
        root_str = normalize_domain_name(target.target)
        root_domain = self._make_domain_doc(root_str, target)
        context["root_domain"] = root_domain
        context["seen_domains"] = {root_str}
        self._emit_document(root_domain)
        log.info(
            "[%s] Emitted root domain (%s)",
            self.actor_name,
            root_domain.id,
        )
        return (1, 0)

    def _parse_line(
        self, line: str, target: TargetDocument, context: Dict[str, Any]
    ) -> Optional[Tuple[CanonicalDocument | None, CanonicalDocument | None]]:
        if not line.strip():
            return None
        domain_str = normalize_domain_name(line)

        root_domain = context.get("root_domain")
        if root_domain is None:
            root_str = normalize_domain_name(target.target)
            root_domain = self._make_domain_doc(root_str, target)
            context["root_domain"] = root_domain
            context["seen_domains"] = {root_str}
            self._emit_document(root_domain)

        seen = context.setdefault("seen_domains", set())
        if domain_str in seen:
            return None
        seen.add(domain_str)

        child = self._make_domain_doc(domain_str, target)
        relation = build_relation(
            root_domain.id,
            child.id,
            "related-to",
            "subfinder discovery",
            target,
            "subfinder",
        )
        return (child, relation)
