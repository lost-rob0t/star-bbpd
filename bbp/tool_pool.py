#!/usr/bin/env python3

from __future__ import annotations

import itertools
import logging
import threading
from typing import Any, Dict, List

import pykka

from bbp.tool_actor import ScanTarget
from bbp.tools import TOOL_CLASSES

log = logging.getLogger(__name__)


class ToolActorPool:
    """Round-robin pool of tool actor instances."""

    def __init__(
        self,
        tool_name: str,
        pool_size: int,
        publisher_ref: Any,
        tool_config: Dict[str, Any],
        routing_config: Dict[str, Any],
        shutdown_timeout: float = 60,
    ):
        if pool_size <= 0:
            raise ValueError("tool actor pool size must be positive")

        tool_class = TOOL_CLASSES.get(tool_name)
        if not tool_class:
            raise ValueError(f"Unknown tool actor: {tool_name}")

        self.tool_name = tool_name
        self.actors: List[pykka.ActorRef] = []
        self.round_robin = itertools.cycle(range(pool_size))
        self._lock = threading.Lock()
        self.shutdown_timeout = shutdown_timeout

        for index in range(pool_size):
            actor_ref = tool_class.start(
                f"{tool_name}-{index}", publisher_ref, tool_config
            )
            actor_ref.proxy().routing = routing_config
            self.actors.append(actor_ref)

    def submit_target(self, scan: ScanTarget) -> None:
        with self._lock:
            actor_ref = self.actors[next(self.round_robin)]
        actor_ref.tell(scan)

    def stop(self, timeout: float | None = None) -> None:
        wait = self.shutdown_timeout if timeout is None else timeout
        for actor_ref in self.actors:
            actor_ref.stop(block=True, timeout=wait)
        self.actors.clear()

    def get_proxy(self) -> "ToolActorPoolProxy":
        return ToolActorPoolProxy(self)


class ToolActorPoolProxy:
    """Actor-like facade for a tool actor pool."""

    def __init__(self, pool: ToolActorPool):
        self.pool = pool

    def tell(self, message):
        if not isinstance(message, ScanTarget):
            log.warning(
                "[%s] Unknown pool message type: %s",
                self.pool.tool_name,
                type(message),
            )
            return
        self.pool.submit_target(message)

    def stop(self, timeout: float | None = None) -> None:
        self.pool.stop(timeout)


def create_tool_pools(config, publisher_ref, routing_config) -> Dict[str, ToolActorPool]:
    pools: Dict[str, ToolActorPool] = {}
    pool_size = config.service.get("tool_pool_size", 2)
    shutdown_timeout = float(config.service.get("shutdown_timeout_sec", 60))

    for tool_name in config.enabled_actors():
        pools[tool_name] = ToolActorPool(
            tool_name,
            pool_size,
            publisher_ref,
            config.get_tool_config(tool_name),
            routing_config,
            shutdown_timeout,
        )

    return pools
