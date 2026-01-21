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
from bbp.publisher import Publisher
from bbp.consumer import TargetConsumer
from bbp.events import create_event
from bbp.tools import TOOL_CLASSES


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
        publisher: Publisher actor reference
        event_type: Event type (service-start, service-ready, service-shutdown)
        details: Event details
        routing_config: Routing configuration
    """
    from bbp.publisher import PublishEvent

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
        rabbit_manager.declare_topology(
            enabled_actors, config.routing.get("target_key")
        )

        # Start Publisher actor
        log.info("Starting Publisher actor...")
        publisher = Publisher.start(rabbit_manager, config.data).proxy()

        # Emit service-start event
        emit_service_event(
            publisher, "service-start", "BBP Actor Service starting", config.routing
        )

        # Start Tool Actors
        log.info("Starting Tool Actors...")
        tool_actors: Dict[str, Any] = {}
        for actor_name in config.enabled_actors():
            tool_class = TOOL_CLASSES.get(actor_name)
            if not tool_class:
                log.error(f"Unknown tool actor: {actor_name}")
                continue

            tool_config = config.get_tool_config(actor_name)
            tool_actor = tool_class.start(actor_name, publisher, tool_config).proxy()

            # Set routing config on tool actor
            tool_actor.routing = config.routing

            tool_actors[actor_name] = tool_actor
            log.info(f"Started {actor_name} tool actor")

        # Start Target Consumers
        log.info("Starting Target Consumers...")
        consumers: List[Any] = []
        for actor_name in config.enabled_actors():
            actor_config = config.get_actor_config(actor_name)
            consumer = TargetConsumer.start(
                actor_name,
                rabbit_manager,
                publisher,
                tool_actors[actor_name],
                actor_config,
                config.routing,
            ).proxy()
            consumers.append(consumer)
            log.info(f"Started {actor_name} target consumer")

        # Emit service-ready event
        emit_service_event(
            publisher, "service-ready", "BBP Actor Service ready", config.routing
        )
        log.info("BBP Actor Service is ready")

        # Setup signal handlers for graceful shutdown
        def signal_handler(sig, frame):
            log.info(f"Received signal {sig}, shutting down...")
            shutdown(consumers, tool_actors, publisher, rabbit_manager, config.routing)

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
    consumers: List[Any],
    tool_actors: Dict[str, Any],
    publisher: Any,
    rabbit_manager: RabbitManager,
    routing_config: Dict[str, Any],
):
    """
    Graceful shutdown sequence.

    Args:
        consumers: List of TargetConsumer actor references
        tool_actors: Dict of tool actor references
        publisher: Publisher actor reference
        rabbit_manager: RabbitManager instance
        routing_config: Routing configuration
    """
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
    for consumer in consumers:
        try:
            consumer.stop()
        except Exception as e:
            log.warning(f"Error stopping consumer: {e}")

    # Wait for in-flight scans to complete (with timeout)
    log.info("Waiting for in-flight scans to complete...")
    time.sleep(2)  # Give scans time to finish

    # Stop tool actors
    log.info("Stopping Tool Actors...")
    for actor_name, actor in tool_actors.items():
        try:
            actor.stop()
        except Exception as e:
            log.warning(f"Error stopping {actor_name}: {e}")

    # Stop publisher (flush pending publishes)
    log.info("Stopping Publisher...")
    try:
        time.sleep(1)  # Give publisher time to flush
        publisher.stop()
    except Exception as e:
        log.warning(f"Error stopping publisher: {e}")

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
