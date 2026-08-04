#!/usr/bin/env python3

import logging
import time
import threading
from typing import Any, Dict, List, Optional

import pika
from pika.exceptions import AMQPConnectionError, AMQPChannelError

from bbp.topology import TopologyPlan, declare_topology

log = logging.getLogger(__name__)


class RabbitManager:
    """
    Manages RabbitMQ connection, channels, and topology.

    Provides thread-safe channel access with automatic reconnection
    and exponential backoff.
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.host = config.get("host", "localhost")
        self.port = config.get("port", 5672)
        self.vhost = config.get("vhost", "/")
        self.username = config.get("username", "guest")
        self.password = config.get("password", "guest")
        self.heartbeat = config.get("heartbeat_sec", 30)
        self.connection: Optional[pika.BlockingConnection] = None
        self._lock = threading.Lock()
        self._publish_channel: Optional[pika.channel.Channel] = None
        self._consume_channels: Dict[str, pika.channel.Channel] = {}
        self._backoff_delays = [1, 2, 4, 8, 16, 30]  # exponential backoff (max 30s)
        self._retry_count = 0

    def connection_parameters(self, connection_name: Optional[str] = None) -> pika.ConnectionParameters:
        """Build pika ConnectionParameters from our config.

        NOTE: pika connections/channels are NOT thread-safe; use one connection per thread.
        """
        credentials = pika.PlainCredentials(self.username, self.password)
        client_properties = {"connection_name": connection_name} if connection_name else None
        return pika.ConnectionParameters(
            host=self.host,
            port=self.port,
            virtual_host=self.vhost,
            credentials=credentials,
            heartbeat=self.heartbeat,
            client_properties=client_properties,
        )

    def new_connection(self, connection_name: Optional[str] = None) -> pika.BlockingConnection:
        """Create a fresh BlockingConnection (use this per-thread)."""
        return pika.BlockingConnection(self.connection_parameters(connection_name))

    def connect(self) -> bool:
        """
        Connect to RabbitMQ with exponential backoff on failure.

        Returns:
            True if connected successfully, False otherwise
        """
        while self._retry_count < len(self._backoff_delays):
            try:
                log.info(
                    f"Connecting to RabbitMQ at {self.host}:{self.port}{self.vhost}"
                )
                self.connection = self.new_connection(connection_name="bbp.rabbit")
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
        DEPRECATED: Use new_connection() per-thread instead.

        This method is UNSAFE for multi-threaded use. Pika channels/connections
        are NOT thread-safe. Only use this in single-threaded contexts like topology
        declaration at startup.

        Returns:
            pika channel for publishing

        Raises:
            RuntimeError: if not connected
        """
        log.warning(
            "get_publish_channel() is DEPRECATED and NOT thread-safe. "
            "Use new_connection() per thread for publishing."
        )
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

    def declare_topology(self, plan: TopologyPlan):
        """
        Declare every BBPD exchange, queue, and binding from one topology plan.

        Uses a short-lived connection/channel for topology declaration.
        This is safe since topology is declared once at startup in a single thread.
        """
        log.info("Declaring RabbitMQ topology with short-lived connection")

        topology_conn = self.new_connection(connection_name="bbp.topology")
        channel = None
        try:
            channel = topology_conn.channel()
            declare_topology(channel, plan)
            log.info(
                "RabbitMQ topology declared successfully: %s exchanges, %s bindings",
                len(plan.exchanges),
                len(plan.bindings),
            )
        finally:
            try:
                if channel and channel.is_open:
                    channel.close()
                if topology_conn and topology_conn.is_open:
                    topology_conn.close()
            except Exception as e:
                log.warning(f"Error closing topology connection: {e}")

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
