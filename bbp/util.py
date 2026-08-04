#!/usr/bin/env python3

from bbp.document_boundary import (
    Source,
    TargetDocument,
    derived_sources,
    target_dataset,
)


def propagate_sources(target: TargetDocument, tool_name: str) -> list[Source]:
    """Compatibility name for the centralized 0.9 boundary helper."""

    return derived_sources(target, tool_name)


def propagate_dataset(target: TargetDocument) -> str:
    """Compatibility name for canonical target dataset access."""

    return target_dataset(target)
