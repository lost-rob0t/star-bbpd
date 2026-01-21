#!/usr/bin/env python3

import logging
import threading
from typing import Dict, Any, List, Optional
import itertools

import pykka

from bbp.tools import TOOL_CLASSES
from bbp.consumer import ScanTarget

log = logging.getLogger(__name__)


class ToolActorPool:
    """
    Pool of tool actors for load balancing and parallel processing.
    
    Creates multiple instances of each tool actor and distributes work using round-robin.
    """

    def __init__(self, tool_name: str, pool_size: int, publisher_ref: Any, tool_config: Dict[str, Any], routing_config: Dict[str, Any]):
        self.tool_name = tool_name
        self.pool_size = pool_size
        self.publisher_ref = publisher_ref
        self.tool_config = tool_config
        self.routing_config = routing_config
        
        # Create pool of tool actors
        self.actors: List[pykka.ActorRef] = []
        self.round_robin = itertools.cycle(range(pool_size))
        self._lock = threading.Lock()
        
        # Get tool class
        tool_class = TOOL_CLASSES.get(tool_name)
        if not tool_class:
            raise ValueError(f"Unknown tool actor: {tool_name}")
        
        # Start actor instances
        for i in range(pool_size):
            actor_ref = tool_class.start(f"{tool_name}-{i}", publisher_ref, tool_config)
            actor = actor_ref.proxy()
            actor.routing = routing_config  # Set routing config
            self.actors.append(actor_ref)
            log.info(f"Started {tool_name} tool actor {i}")

    def submit_target(self, target: Any, delivery_tag: int, consumer_ref: Any):
        """
        Submit target to next available actor in pool.
        
        Args:
            target: StarIntel Target object
            delivery_tag: RabbitMQ delivery tag for ack
            consumer_ref: TargetConsumer reference for ack callback
        """
        with self._lock:
            actor_index = next(self.round_robin)
            actor_ref = self.actors[actor_index]
        
        # Send scan target to actor
        actor_ref.tell(ScanTarget(target, delivery_tag, consumer_ref))
        log.debug(f"[{self.tool_name}] Submitted target {target.target} to actor {actor_index}")

    def stop(self):
        """Stop all actors in the pool."""
        log.info(f"Stopping {self.tool_name} tool actor pool...")
        for i, actor_ref in enumerate(self.actors):
            try:
                actor_ref.stop()
                log.info(f"Stopped {self.tool_name} tool actor {i}")
            except Exception as e:
                log.warning(f"Error stopping {self.tool_name} tool actor {i}: {e}")
        self.actors.clear()

    def get_proxy(self):
        """Get a proxy that mimics single tool actor interface."""
        return ToolActorPoolProxy(self)


class ToolActorPoolProxy:
    """
    Proxy that mimics single tool actor interface but uses the pool.
    """

    def __init__(self, pool: ToolActorPool):
        self.pool = pool

    def tell(self, message):
        """Handle tell() calls like a regular actor."""
        if isinstance(message, ScanTarget):
            self.pool.submit_target(message.target, message.delivery_tag, message.consumer_ref)
        else:
            log.warning(f"[{self.pool.tool_name}] ToolActorPoolProxy: Unknown message type: {type(message)}")

    def stop(self):
        """Stop the underlying pool."""
        self.pool.stop()


def create_tool_pools(config, publisher_ref, routing_config) -> Dict[str, ToolActorPool]:
    """
    Create tool actor pools for all enabled tools.
    
    Args:
        config: BBP configuration
        publisher_ref: Publisher pool proxy
        routing_config: Routing configuration
        
    Returns:
        Dict mapping tool names to ToolActorPool instances
    """
    pools = {}
    pool_size = config.service.get("tool_pool_size", 2)
    
    for tool_name in config.enabled_actors():
        tool_config = config.get_tool_config(tool_name)
        try:
            pool = ToolActorPool(tool_name, pool_size, publisher_ref, tool_config, routing_config)
            pools[tool_name] = pool
            log.info(f"Created pool for {tool_name} with {pool_size} actors")
        except Exception as e:
            log.error(f"Failed to create pool for {tool_name}: {e}")
    
    return pools