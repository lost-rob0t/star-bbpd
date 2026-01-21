#!/usr/bin/env python3

import logging
import time
import threading
from typing import Dict, Any, Optional

import pika
from pika.exceptions import AMQPConnectionError, AMQPChannelError

log = logging.getLogger(__name__)


class RabbitManager:
    """
    Manages RabbitMQ connection, channels, and topology.

    Provides thread-safe channel access with automatic reconnection
    and exponential backoff.
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.connection: Optional[pika.BlockingConnection] = None
        self._lock = threading.Lock()
        self._publish_channel: Optional[pika.channel.Channel] = None
        self._consume_channels: Dict[str, pika.channel.Channel] = {}
        self._backoff_delays = [1, 2, 4, 8, 16, 30]  # exponential backoff (max 30s)
        self._retry_count = 0

    def connect(self) -> bool:
        """
        Connect to RabbitMQ with exponential backoff on failure.

        Returns:
            True if connected successfully, False otherwise
        """
        while self._retry_count < len(self._backoff_delays):
            try:
                params = pika.ConnectionParameters(
                    host=self.config.get("host", "localhost"),
                    port=self.config.get("port", 5672),
                    virtual_host=self.config.get("vhost", "/"),
                    credentials=pika.PlainCredentials(
                        username=self.config.get("username", "guest"),
                        password=self.config.get("password", "guest"),
                    ),
                    heartbeat=self.config.get("heartbeat_sec", 30),
                )

                log.info(
                    f"Connecting to RabbitMQ at {params.host}:{params.port}{params.virtual_host}"
                )
                self.connection = pika.BlockingConnection(params)
                log.info("Connected to RabbitMQ successfully")
                self._retry_count = 0  # reset on successful connection
                return True

            except AMQPConnectionError as e:
                delay = self._backoff_delays[self._retry_count]
                log.error(
                    f"Failed to connect to RabbitMQ: {e}. Retrying in {delay}s..."
                )
                time.sleep(delay)
                self._retry_count += 1

        log.error("Failed to connect to RabbitMQ after all retries")
        return False

    def get_publish_channel(self) -> pika.channel.Channel:
        """
        Get a channel for publishing (thread-safe).

        Creates channel on first access and reuses it.

        Returns:
            pika channel for publishing

        Raises:
            RuntimeError: if not connected
        """
        with self._lock:
            if not self.is_connected():
                raise RuntimeError("Not connected to RabbitMQ")

            if self._publish_channel is None or self._publish_channel.is_closed:
                log.debug("Creating new publish channel")
                self._publish_channel = self.connection.channel()

            return self._publish_channel

    def get_consume_channel(self, name: str = "default") -> pika.channel.Channel:
        """
        Get a channel for consuming (thread-safe).

        Creates separate channels per consumer name for isolation.

        Args:
            name: Consumer name (for channel isolation)

        Returns:
            pika channel for consuming

        Raises:
            RuntimeError: if not connected
        """
        with self._lock:
            if not self.is_connected():
                raise RuntimeError("Not connected to RabbitMQ")

            if (
                name not in self._consume_channels
                or self._consume_channels[name].is_closed
            ):
                log.debug(f"Creating new consume channel for: {name}")
                self._consume_channels[name] = self.connection.channel()

            return self._consume_channels[name]

    def declare_topology(self, enabled_actors: list, routing_template: str):
        """
        Declare RabbitMQ exchanges, queues, and bindings.

        Args:
            enabled_actors: List of (actor_name, actor_config) tuples
            routing_template: Template for routing key (e.g., "actors.{actor}.new.target")
        """
        if not self.is_connected():
            raise RuntimeError("Cannot declare topology: not connected")

        channel = self.get_publish_channel()

        # Declare documents exchange
        exchange_name = self.config.get("exchange", "documents")
        exchange_type = self.config.get("exchange_type", "topic")
        log.info(f"Declaring exchange: {exchange_name} (type: {exchange_type})")
        channel.exchange_declare(
            exchange=exchange_name, exchange_type=exchange_type, durable=True
        )

        # Declare queues and bindings for each enabled actor
        for actor_name, actor_config in enabled_actors:
            queue_name = actor_config.get("queue", f"bbp.{actor_name}.targets")
            routing_key = routing_template.format(actor=actor_name)

            log.info(f"Declaring queue: {queue_name} -> {routing_key}")
            channel.queue_declare(queue=queue_name, durable=True)
            channel.queue_bind(
                queue=queue_name, exchange=exchange_name, routing_key=routing_key
            )

        log.info("RabbitMQ topology declared successfully")

    def is_connected(self) -> bool:
        """Check if connected to RabbitMQ"""
        return self.connection is not None and self.connection.is_open

    def close(self):
        """Close all channels and connection"""
        log.info("Closing RabbitMQ connections")

        with self._lock:
            # Close consume channels
            for name, channel in self._consume_channels.items():
                if channel and channel.is_open:
                    try:
                        channel.close()
                        log.debug(f"Closed consume channel: {name}")
                    except Exception as e:
                        log.warning(f"Error closing consume channel {name}: {e}")

            # Close publish channel
            if self._publish_channel and self._publish_channel.is_open:
                try:
                    self._publish_channel.close()
                    log.debug("Closed publish channel")
                except Exception as e:
                    log.warning(f"Error closing publish channel: {e}")

            # Close connection
            if self.connection and self.connection.is_open:
                try:
                    self.connection.close()
                    log.info("Closed RabbitMQ connection")
                except Exception as e:
                    log.warning(f"Error closing connection: {e}")

            self.connection = None
            self._publish_channel = None
            self._consume_channels = {}
