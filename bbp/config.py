#!/usr/bin/env python3

import sys
from pathlib import Path
from typing import Any, Dict, List

if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomli as tomllib
    except ImportError as exc:
        raise ImportError(
            "tomli is required for Python 3.10; install the project dependencies"
        ) from exc


class Config:
    """Validated BBP Actor Service configuration."""

    ROUTING_FIELDS = {
        "target_key": "{actor}",
        "document_ingest_key": "{dtype}",
        "document_update_key": "{dtype}",
        "document_new_key": "{dtype}",
        "event_key": "{eventType}",
    }

    def __init__(self, data: Dict[str, Any]):
        self.data = data
        self._validate()

    @classmethod
    def load(cls, path: str) -> "Config":
        config_path = Path(path)
        if not config_path.is_file():
            raise FileNotFoundError(
                f"Config file not found: {path}. Copy config.example.toml to config.toml first."
            )

        with config_path.open("rb") as stream:
            return cls(tomllib.load(stream))

    def _validate(self) -> None:
        for section in ("service", "documents", "events", "routing"):
            if section not in self.data:
                raise ValueError(f"Missing required config section: [{section}]")

        for field in ("host", "port", "exchange"):
            if field not in self.documents:
                raise ValueError(f"Missing required field in [documents]: {field}")

        if "exchange" not in self.events:
            raise ValueError("Missing required field in [events]: exchange")

        for field, placeholder in self.ROUTING_FIELDS.items():
            template = self.routing.get(field)
            if not isinstance(template, str) or not template:
                raise ValueError(f"Missing required field in [routing]: {field}")
            if placeholder not in template:
                raise ValueError(
                    f"[routing].{field} must contain the {placeholder} placeholder"
                )

        self._require_positive_int(
            "service.publisher_pool_size", self.service.get("publisher_pool_size", 3)
        )
        self._require_positive_int(
            "service.tool_pool_size", self.service.get("tool_pool_size", 2)
        )
        self._require_positive_int(
            "service.publisher_max_retries",
            self.service.get("publisher_max_retries", 3),
        )
        self._require_positive_number(
            "service.publish_settlement_timeout_sec",
            self.service.get("publish_settlement_timeout_sec", 30),
        )
        self._require_positive_number(
            "service.shutdown_timeout_sec",
            self.service.get("shutdown_timeout_sec", 60),
        )
        delays = self.service.get("publisher_retry_delays_sec", [1, 2, 4])
        if not isinstance(delays, list) or not delays:
            raise ValueError("service.publisher_retry_delays_sec must be a non-empty list")
        for index, delay in enumerate(delays):
            self._require_nonnegative_number(
                f"service.publisher_retry_delays_sec[{index}]", delay
            )

        enabled = self.enabled_actors()
        if not enabled:
            raise ValueError("No actors enabled. Enable at least one actor in config")

        for actor in enabled:
            actor_config = self.get_actor_config(actor)
            self._require_positive_int(
                f"actors.{actor}.prefetch", actor_config.get("prefetch", 1)
            )
            if actor not in self.tools:
                raise ValueError(
                    f"Actor '{actor}' is enabled but missing [tools.{actor}] section"
                )
            tool_config = self.get_tool_config(actor)
            self._require_positive_int(
                f"tools.{actor}.timeout_sec",
                tool_config.get("timeout_sec", 300),
            )
            self._require_positive_number(
                f"tools.{actor}.publish_timeout_sec",
                tool_config.get("publish_timeout_sec", 30),
            )

        dns = self.data.get("dns_resolver", {})
        if dns.get("enabled", False):
            self._require_positive_int(
                "dns_resolver.prefetch", dns.get("prefetch", 5)
            )
            self._require_positive_number(
                "dns_resolver.timeout_sec", dns.get("timeout_sec", 5)
            )
            self._require_positive_number(
                "dns_resolver.publish_timeout_sec",
                dns.get("publish_timeout_sec", 30),
            )
            self._require_positive_number(
                "dns_resolver.shutdown_timeout_sec",
                dns.get("shutdown_timeout_sec", 60),
            )

    @staticmethod
    def _require_positive_int(name: str, value: Any) -> None:
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise ValueError(f"{name} must be a positive integer")

    @staticmethod
    def _require_positive_number(name: str, value: Any) -> None:
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or value <= 0
        ):
            raise ValueError(f"{name} must be a positive number")

    @staticmethod
    def _require_nonnegative_number(name: str, value: Any) -> None:
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or value < 0
        ):
            raise ValueError(f"{name} must be a non-negative number")

    @property
    def service(self) -> Dict[str, Any]:
        return self.data["service"]

    @property
    def documents(self) -> Dict[str, Any]:
        return self.data["documents"]

    @property
    def events(self) -> Dict[str, Any]:
        return self.data["events"]

    @property
    def routing(self) -> Dict[str, Any]:
        return self.data["routing"]

    @property
    def actors(self) -> Dict[str, Dict[str, Any]]:
        return self.data.get("actors", {})

    @property
    def tools(self) -> Dict[str, Dict[str, Any]]:
        return self.data.get("tools", {})

    def enabled_actors(self) -> List[str]:
        return [
            name
            for name, actor_config in self.actors.items()
            if actor_config.get("enabled", False)
        ]

    def get_actor_config(self, actor_name: str) -> Dict[str, Any]:
        if actor_name not in self.actors:
            raise ValueError(f"No configuration found for actor: {actor_name}")
        return self.actors[actor_name]

    def get_tool_config(self, tool_name: str) -> Dict[str, Any]:
        if tool_name not in self.tools:
            raise ValueError(f"No configuration found for tool: {tool_name}")
        return self.tools[tool_name]
