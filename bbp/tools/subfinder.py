#!/usr/bin/env python3

import logging
from typing import Any, Dict, List, Optional, Tuple

from bbp.tool_actor import ToolActor
from bbp.util import propagate_sources, propagate_dataset

from starintel_doc.hosts import Domain
from starintel_doc.relations import Relation

log = logging.getLogger(__name__)


class SubfinderActor(ToolActor):
    """
    Subfinder tool actor.

    Consumes domain targets, runs subfinder, emits:
      - a root Domain doc for the target domain (once per scan)
      - Domain docs for discovered subdomains
      - Relation: root_domain -> subdomain (NOT target -> subdomain)
    """

    def _build_command(self, target: Any) -> List[str]:
        bin_path = self.tool_config.get("bin", "subfinder")
        args = self.tool_config.get("args", [])

        cmd = [bin_path]

        if "-d" not in args:
            cmd.extend(args)
            cmd.extend(["-d", target.target])
        else:
            cmd.extend([arg if arg != "{target}" else target.target for arg in args])

        return cmd

    def _make_domain_doc(self, domain_str: str, target: Any) -> Domain:
        dataset = propagate_dataset(target)
        sources = propagate_sources(target, "subfinder")

        doc = Domain(
            record_type="A",
            record=domain_str,
            resolved_addresses=[],
            dataset=dataset,
            sources=sources,
        )
        doc.set_meta(dataset)
        return doc

    def _pre_scan(self, target: Any, context: Dict[str, Any]) -> Tuple[int, int]:
        root_str = (getattr(target, "target", "") or "").strip()
        if not root_str:
            return (0, 0)

        root_domain = self._make_domain_doc(root_str, target)
        context["root_domain"] = root_domain
        context["seen_domains"] = {root_str}

        self._emit_document(root_domain)
        log.info(f"[{self.actor_name}] Emitted root domain for target: {root_str} ({root_domain.id})")

        return (1, 0)

    def _parse_line(
        self, line: str, target: Any, context: Dict[str, Any]
    ) -> Optional[Tuple[Any, Any]]:
        domain_str = line.strip()
        if not domain_str:
            return None

        root_domain = context.get("root_domain")
        if root_domain is None:
            root_str = (getattr(target, "target", "") or "").strip()
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

        relation = Relation(
            source=root_domain.id,
            target=subdomain.id,
            predicate="subdomain",
            note="subdomain",
            dataset=propagate_dataset(target),
            sources=propagate_sources(target, "subfinder"),
        )
        relation.set_meta(propagate_dataset(target))

        return (subdomain, relation)
