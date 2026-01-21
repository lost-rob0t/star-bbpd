#!/usr/bin/env python3

from bbp.tools.subfinder import SubfinderActor
from bbp.tools.httpx import HttpxActor
from bbp.tools.katana import KatanaActor
from bbp.tools.nmap import NmapActor

# Tool class registry
TOOL_CLASSES = {
    "subfinder": SubfinderActor,
    "httpx": HttpxActor,
    "katana": KatanaActor,
    "nmap": NmapActor,
}

__all__ = ["TOOL_CLASSES", "SubfinderActor", "HttpxActor", "KatanaActor", "NmapActor"]
