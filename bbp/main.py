#!/usr/bin/env python3

"""BBP Actor Service entry point."""

import logging
import signal
import sys
import time
from typing import Any, Dict, List, Optional

import pykka

from bbp.config import Config
from bbp.consumer import TargetConsumer
from bbp.dns_resolver import DnsResolverActor
from bbp.events import create_event
from bbp.publisher_pool import PublishEvent, PublisherPool, PublisherPoolProxy
from bbp.rabbit import RabbitManager
from bbp.tool_pool import create_tool_pools

log = logging.getLogger(__name__)


def setup_logging(log_level: str = "INFO", log_file: Optional[str] = None) -> None:
    handlers: List[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_file:
        handlers.append(logging.FileHandler(log_file))

    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    for handler in handlers:
        handler.setFormatter(formatter)

    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        handlers=handlers,
    )


def emit_service_event(
    publisher: PublisherPoolProxy,
    event_type: str,
    details: str,
    routing_config: Dict[str, Any],
) -> None:
    event = create_event("bbp-service", event_type, details, "")
    routing_key = routing_config["event_key"].format(eventType=event_type)
    publisher.tell(PublishEvent(event, routing_key))


def declare_dns_queue(rabbit_manager: RabbitManager, config: Config) -> None:
    dns_config = config.data.get("dns_resolver", {})
    if not dns_config.get("enabled", False):
        return

    connection = rabbit_manager.new_connection(connection_name="bbp.dns-topology")
    channel = None
    try:
        channel = connection.channel()
        queue_name = dns_config.get("queue", "bbp.dns-resolver.domains")
        exchange_name = config.documents.get("exchange", "documents")
        routing_key = config.routing["document_new_key"].format(dtype="domain")
        channel.queue_declare(queue=queue_name, durable=True)
        channel.queue_bind(
            queue=queue_name,
            exchange=exchange_name,
            routing_key=routing_key,
        )
        log.info("Declared DNS queue %s -> %s", queue_name, routing_key)
    finally:
        if channel and channel.is_open:
            channel.close()
        if connection.is_open:
            connection.close()


def main() -> None:
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.toml"

    try:
        config = Config.load(config_path)
        setup_logging(
            config.service.get("log_level", "INFO"),
            config.service.get("log_file"),
        )

        rabbit_manager = RabbitManager(config.documents)
        if not rabbit_manager.connect():
            raise RuntimeError("failed to connect to RabbitMQ")

        enabled_actors = [
            (name, config.get_actor_config(name)) for name in config.enabled_actors()
        ]
        rabbit_manager.declare_topology(enabled_actors, config.routing["target_key"])
        declare_dns_queue(rabbit_manager, config)

        publisher_pool = PublisherPool(
            rabbit_manager,
            config.data,
            config.service.get("publisher_pool_size", 3),
        )
        publisher = publisher_pool.get_proxy()
        emit_service_event(
            publisher,
            "service-start",
            "BBP Actor Service starting",
            config.routing,
        )

        tool_pools = create_tool_pools(config, publisher, config.routing)
        missing_pools = set(config.enabled_actors()) - set(tool_pools)
        if missing_pools:
            raise RuntimeError(
                f"failed to create tool pools: {', '.join(sorted(missing_pools))}"
            )

        dns_resolver_ref = None
        dns_config = config.data.get("dns_resolver", {})
        if dns_config.get("enabled", False):
            dns_resolver_ref = DnsResolverActor.start(
                config.documents,
                publisher,
                dns_config,
                config.routing,
            )

        consumer_refs: List[Any] = []
        for actor_name in config.enabled_actors():
            consumer_refs.append(
                TargetConsumer.start(
                    actor_name,
                    config.documents,
                    publisher,
                    tool_pools[actor_name].get_proxy(),
                    config.get_actor_config(actor_name),
                    config.routing,
                )
            )

        emit_service_event(
            publisher,
            "service-ready",
            "BBP Actor Service ready",
            config.routing,
        )
        log.info("BBP Actor Service is ready")

        def signal_handler(sig, _frame):
            log.info("Received signal %s", sig)
            shutdown(
                consumer_refs,
                tool_pools,
                publisher_pool,
                rabbit_manager,
                config.routing,
                publisher,
                dns_resolver_ref,
            )

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        while True:
            time.sleep(1)
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        log.error("Startup failed: %s", exc)
        raise SystemExit(1) from exc
    except Exception as exc:
        log.error("Fatal error: %s", exc, exc_info=True)
        raise SystemExit(1) from exc


def shutdown(
    consumer_refs: List[Any],
    tool_pools: Dict[str, Any],
    publisher_pool: PublisherPool,
    rabbit_manager: RabbitManager,
    routing_config: Dict[str, Any],
    publisher: PublisherPoolProxy,
    dns_resolver_ref: Optional[Any] = None,
) -> None:
    emit_service_event(
        publisher,
        "service-shutdown",
        "BBP Actor Service shutting down",
        routing_config,
    )

    if dns_resolver_ref:
        dns_resolver_ref.stop()
    for consumer_ref in consumer_refs:
        consumer_ref.stop()

    time.sleep(2)
    for pool in tool_pools.values():
        pool.stop()

    time.sleep(2)
    publisher_pool.stop()
    rabbit_manager.close()
    pykka.ActorRegistry.stop_all()
    raise SystemExit(0)


if __name__ == "__main__":
    main()
