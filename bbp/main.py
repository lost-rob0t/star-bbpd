#!/usr/bin/env python3

"""BBP Actor Service - Main entry point"""

import logging
import signal
import sys
import time
from typing import Dict, Any, List

import pykka

from bbp.config import Config
from bbp.rabbit import RabbitManager
from bbp.publisher_pool import PublisherPool
from bbp.consumer import TargetConsumer
from bbp.events import create_event
from bbp.tool_pool import create_tool_pools


log = logging.getLogger(__name__)


def setup_logging(log_level: str = "INFO"):
    """Setup logging configuration"""
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def emit_service_event(
    publisher: Any, event_type: str, details: str, routing_config: Dict[str, Any]
):
    """
    Emit a service-level event.

    Args:
        publisher: Publisher pool proxy
        event_type: Event type (service-start, service-ready, service-shutdown)
        details: Event details
        routing_config: Routing configuration
    """
    from bbp.publisher_pool import PublishEvent

    event = create_event("bbp-service", event_type, details, "")
    routing_key = routing_config["event_key"].format(eventType=event_type)
    publisher.tell(PublishEvent(event, routing_key))


def main():
    """Main entry point"""
    # Parse command line args
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.toml"

    try:
        # Load and validate configuration
        log.info(f"Loading configuration from: {config_path}")
        config = Config.load(config_path)
        log.info("Configuration loaded and validated successfully")

        # Setup logging
        setup_logging(config.service.get("log_level", "INFO"))

        # Initialize RabbitMQ connection
        log.info("Connecting to RabbitMQ...")
        rabbit_manager = RabbitManager(config.documents)
        if not rabbit_manager.connect():
            log.error("Failed to connect to RabbitMQ. Exiting.")
            sys.exit(1)

        # Declare topology (exchanges, queues, bindings)
        log.info("Declaring RabbitMQ topology...")
        enabled_actors = [
            (name, config.get_actor_config(name)) for name in config.enabled_actors()
        ]
        routing_template = config.routing.get("target_key", "actors.{actor}.new.target")
        rabbit_manager.declare_topology(
            enabled_actors, routing_template
        )

        # Start Publisher pool
        pool_size = config.service.get("publisher_pool_size", 3)
        log.info(f"Starting Publisher pool with {pool_size} workers...")
        publisher_pool = PublisherPool(rabbit_manager, config.data, pool_size)
        publisher = publisher_pool.get_proxy()
        publisher_ref = publisher_pool  # Store reference for shutdown

        # Emit service-start event
        emit_service_event(
            publisher, "service-start", "BBP Actor Service starting", config.routing
        )

        # Start Tool Actor Pools
        log.info("Starting Tool Actor Pools...")
        tool_pools = create_tool_pools(config, publisher, config.routing)
        tool_pool_proxies: Dict[str, Any] = {}
        for tool_name, pool in tool_pools.items():
            proxy = pool.get_proxy()
            tool_pool_proxies[tool_name] = proxy
            log.info(f"Started {tool_name} tool actor pool")

        # Start Target Consumers
        log.info("Starting Target Consumers...")
        consumers: List[Any] = []
        consumer_refs: List[Any] = []
        for actor_name in config.enabled_actors():
            actor_config = config.get_actor_config(actor_name)
            consumer_ref = TargetConsumer.start(
                actor_name,
                config.documents,  # Pass rabbit config instead of manager
                publisher_ref,
                tool_pool_proxies[actor_name],
                actor_config,
                config.routing,
            )
            consumer = consumer_ref.proxy()
            consumers.append(consumer)
            consumer_refs.append(consumer_ref)
            log.info(f"Started {actor_name} target consumer")

        # Emit service-ready event
        emit_service_event(
            publisher, "service-ready", "BBP Actor Service ready", config.routing
        )
        log.info("BBP Actor Service is ready")

        # Setup signal handlers for graceful shutdown
        def signal_handler(sig, frame):
            log.info(f"Received signal {sig}, shutting down...")
            shutdown(consumer_refs, tool_pools, publisher_ref, rabbit_manager, config.routing)

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        # Wait forever (actors run in background)
        log.info("Service running. Press Ctrl+C to stop.")
        while True:
            time.sleep(1)

    except FileNotFoundError as e:
        log.error(f"Configuration file not found: {e}")
        sys.exit(1)
    except ValueError as e:
        log.error(f"Configuration error: {e}")
        sys.exit(1)
    except Exception as e:
        log.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


def shutdown(
    consumer_refs: List[Any],
    tool_pools: Dict[str, Any],
    publisher_ref: Any,
    rabbit_manager: RabbitManager,
    routing_config: Dict[str, Any],
):
    """
    Graceful shutdown sequence.

    Args:
        consumer_refs: List of TargetConsumer actor references
        tool_pools: Dict of tool actor pools
        publisher_ref: PublisherPool instance
        rabbit_manager: RabbitManager instance
        routing_config: Routing configuration
    """
    # Get publisher proxy for event emission
    publisher = publisher_ref.get_proxy()
    log.info("Shutting down BBP Actor Service...")

    # Emit service-shutdown event
    emit_service_event(
        publisher,
        "service-shutdown",
        "BBP Actor Service shutting down",
        routing_config,
    )

    # Stop consumers (stop receiving new targets)
    log.info("Stopping Target Consumers...")
    for consumer_ref in consumer_refs:
        try:
            consumer_ref.stop()
        except Exception as e:
            log.warning(f"Error stopping consumer: {e}")

    # Wait for in-flight scans to complete (with timeout)
    log.info("Waiting for in-flight scans to complete...")
    time.sleep(2)  # Give scans time to finish

    # Stop tool actor pools
    log.info("Stopping Tool Actor Pools...")
    for pool_name, pool in tool_pools.items():
        try:
            pool.stop()
        except Exception as e:
            log.warning(f"Error stopping {pool_name} pool: {e}")

    # Stop publisher pool (flush pending publishes)
    log.info("Stopping Publisher pool...")
    try:
        time.sleep(2)  # Give publishers time to flush
        publisher_ref.stop()
    except Exception as e:
        log.warning(f"Error stopping publisher pool: {e}")

    # Close RabbitMQ connections
    log.info("Closing RabbitMQ connections...")
    rabbit_manager.close()

    # Shutdown Pykka actor system
    log.info("Shutting down Pykka actor system...")
    pykka.ActorRegistry.stop_all()

    log.info("BBP Actor Service stopped")
    sys.exit(0)


if __name__ == "__main__":
    main()
