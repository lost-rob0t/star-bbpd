#!/usr/bin/env python3

from bbp.manifest_catalog import register_extended_manifests
from bbp.tools.bbot import BbotActor
from bbp.tools.http_proxy import HttpProxyActor
from bbp.tools.httpx import HttpxActor
from bbp.tools.katana import KatanaActor
from bbp.tools.nmap import NmapActor
from bbp.tools.nuclei import NucleiActor
from bbp.tools.screenshot import ScreenshotActor
from bbp.tools.subfinder import SubfinderActor

TOOL_CLASSES = {
    "bbot": BbotActor,
    "http-proxy": HttpProxyActor,
    "httpx": HttpxActor,
    "katana": KatanaActor,
    "nmap": NmapActor,
    "nuclei": NucleiActor,
    "screenshot": ScreenshotActor,
    "subfinder": SubfinderActor,
}

register_extended_manifests()

__all__ = [
    "TOOL_CLASSES",
    "BbotActor",
    "HttpProxyActor",
    "HttpxActor",
    "KatanaActor",
    "NmapActor",
    "NucleiActor",
    "ScreenshotActor",
    "SubfinderActor",
]
