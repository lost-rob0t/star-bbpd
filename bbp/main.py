#!/usr/bin/env python3

"""BBP Actor Service entry point."""

from __future__ import annotations

import logging
import signal
import sys
import time
from typing import Any, Dict, List, Optional

import pykka

from bbp.config import Config
from bbp.consumer import StopIntake, TargetConsumer
from bbp.dns_resolver import DnsResolverActor
from bbp.events import create_event
from bbp.manifests import publish_actor_manifests
from bbp.publisher_pool import PublishEvent, PublisherPool, PublisherPoolProxy
from bbp.rabbit import RabbitManager
from bbp.tool_pool import create_tool_pools
from bbp.topology import build_topology

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
        rabbit_manager.declare_topology(build_topology(config))

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
        manifest_count = publish_actor_manifests(config, publisher)
        log.info("Published %s actor manifests before runtime readiness", manifest_count)

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
            actor_config = dict(config.get_actor_config(actor_name))
            actor_config.setdefault(
                "shutdown_timeout_sec",
                config.service.get("shutdown_timeout_sec", 60),
            )
            consumer_refs.append(
                TargetConsumer.start(
                    actor_name,
                    config.documents,
                    publisher,
                    tool_pools[actor_name].get_proxy(),
                    actor_config,
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
                float(config.service.get("shutdown_timeout_sec", 60)),
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


def _stop_actor(actor_ref: Any, timeout: float, name: str) -> None:
    try:
        actor_ref.stop(block=True, timeout=timeout)
    except Exception as exc:
        log.error("Failed to stop %s cleanly: %s", name, exc)
        raise


def shutdown(
    consumer_refs: List[Any],
    tool_pools: Dict[str, Any],
    publisher_pool: PublisherPool,
    rabbit_manager: RabbitManager,
    routing_config: Dict[str, Any],
    publisher: PublisherPoolProxy,
    dns_resolver_ref: Optional[Any] = None,
    shutdown_timeout: float = 60,
) -> None:
    emit_service_event(
        publisher,
        "service-shutdown",
        "BBP Actor Service shutting down after in-flight settlement",
        routing_config,
    )

    # Stop new target intake while keeping consumer actors alive to receive
    # settlement ACK/NACK messages from in-flight tool actors.
    for index, consumer_ref in enumerate(consumer_refs):
        consumer_ref.ask(StopIntake(), block=True, timeout=shutdown_timeout)
        log.info("Stopped intake for consumer-%s", index)

    if dns_resolver_ref:
        _stop_actor(dns_resolver_ref, shutdown_timeout, "dns-resolver")

    for name, pool in tool_pools.items():
        pool.stop(shutdown_timeout)
        log.info("Stopped tool pool %s after settlement", name)

    # ACK/NACK messages are now queued before each stop message, so consumer
    # actors drain those settlement decisions before exiting.
    for index, consumer_ref in enumerate(consumer_refs):
        _stop_actor(consumer_ref, shutdown_timeout, f"consumer-{index}")

    publisher_pool.stop(shutdown_timeout)
    rabbit_manager.close()
    pykka.ActorRegistry.stop_all(block=True, timeout=shutdown_timeout)
    raise SystemExit(0)


if __name__ == "__main__":
    main()
