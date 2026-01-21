#!/usr/bin/env python3

from typing import List


def propagate_sources(target, tool_name: str) -> List[str]:
    """
    Propagate sources from target and append tool provenance.

    Args:
        target: Target document object with .sources attribute
        tool_name: Name of the tool (e.g., "subfinder", "nmap")

    Returns:
        List of sources with tool:<tool_name> appended

    Example:
        >>> target.sources = ["manual:user123", "tool:recon"]
        >>> propagate_sources(target, "subfinder")
        ["manual:user123", "tool:recon", "tool:subfinder"]
    """
    tool_source = f"tool:{tool_name}"

    # Start with target sources if present
    if hasattr(target, "sources") and target.sources:
        result = list(target.sources)  # copy list
    else:
        result = []

    # Append tool source if not already present
    if tool_source not in result:
        result.append(tool_source)

    return result


def propagate_dataset(target) -> str:
    """
    Get dataset from target document.

    Args:
        target: Target document object with .dataset attribute

    Returns:
        Dataset string from target

    Raises:
        AttributeError: If target has no dataset attribute
    """
    if not hasattr(target, "dataset"):
        raise AttributeError("Target document missing required 'dataset' attribute")

    return target.dataset
