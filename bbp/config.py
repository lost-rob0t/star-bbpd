#!/usr/bin/env python3

import sys
from pathlib import Path
from typing import Any, Dict, List

# Python 3.11+ has built-in tomllib, older versions need tomli
if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomli as tomllib
    except ImportError:
        raise ImportError(
            "tomli is required for Python < 3.11. Install with: pip install tomli"
        )


class Config:
    """BBP Actor Service configuration loaded from TOML"""

    def __init__(self, data: Dict[str, Any]):
        self.data = data
        self._validate()

    @classmethod
    def load(cls, path: str) -> "Config":
        """Load configuration from TOML file"""
        config_path = Path(path)
        if not config_path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")

        with open(config_path, "rb") as f:
            data = tomllib.load(f)

        return cls(data)

    def _validate(self):
        """Validate configuration has required sections and fields"""
        # Required sections
        required_sections = ["service", "documents", "events", "routing"]
        for section in required_sections:
            if section not in self.data:
                raise ValueError(f"Missing required config section: [{section}]")

        # Required fields in [documents]
        required_doc_fields = ["host", "port", "exchange"]
        for field in required_doc_fields:
            if field not in self.data["documents"]:
                raise ValueError(f"Missing required field in [documents]: {field}")

        # Required fields in [events]
        if "exchange" not in self.data["events"]:
            raise ValueError("Missing required field in [events]: exchange")

        # Required fields in [routing]
        required_routing = ["target_key", "doc_key", "event_key"]
        for field in required_routing:
            if field not in self.data["routing"]:
                raise ValueError(f"Missing required field in [routing]: {field}")

        # At least one actor enabled
        enabled = self.enabled_actors()
        if not enabled:
            raise ValueError("No actors enabled. Enable at least one actor in config")

        # Each enabled actor must have tool config
        for actor in enabled:
            tool_section = f"tools.{actor}"
            if "tools" not in self.data or actor not in self.data["tools"]:
                raise ValueError(
                    f"Actor '{actor}' is enabled but missing [{tool_section}] section"
                )

    @property
    def service(self) -> Dict[str, Any]:
        """Get [service] configuration"""
        return self.data["service"]

    @property
    def documents(self) -> Dict[str, Any]:
        """Get [documents] configuration"""
        return self.data["documents"]

    @property
    def events(self) -> Dict[str, Any]:
        """Get [events] configuration"""
        return self.data["events"]

    @property
    def routing(self) -> Dict[str, Any]:
        """Get [routing] configuration"""
        return self.data["routing"]

    @property
    def actors(self) -> Dict[str, Dict[str, Any]]:
        """Get all [actors.*] configurations"""
        if "actors" in self.data:
            return self.data["actors"]
        return {}

    @property
    def tools(self) -> Dict[str, Dict[str, Any]]:
        """Get all [tools.*] configurations"""
        result = {}
        if "tools" in self.data:
            return self.data["tools"]
        return result

    def enabled_actors(self) -> List[str]:
        """Get list of enabled actor names"""
        enabled = []
        actors = self.actors
        for name, config in actors.items():
            if config.get("enabled", False):
                enabled.append(name)
        return enabled

    def get_actor_config(self, actor_name: str) -> Dict[str, Any]:
        """Get configuration for specific actor"""
        actors = self.actors
        if actor_name not in actors:
            raise ValueError(f"No configuration found for actor: {actor_name}")
        return actors[actor_name]

    def get_tool_config(self, tool_name: str) -> Dict[str, Any]:
        """Get configuration for specific tool"""
        if tool_name not in self.tools:
            raise ValueError(f"No configuration found for tool: {tool_name}")
        return self.tools[tool_name]
