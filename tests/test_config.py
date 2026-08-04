from pathlib import Path

import pytest

from bbp.config import Config


ROOT = Path(__file__).resolve().parents[1]


def test_example_config_is_safe_and_valid():
    config = Config.load(str(ROOT / "config.example.toml"))

    assert config.documents["host"] == "localhost"
    assert config.documents["password"] == "replace-me"
    assert config.routing["document_ingest_key"] == "documents.ingest.{dtype}"
    assert config.routing["document_update_key"] == "documents.updated.{dtype}"
    assert config.routing["document_new_key"] == "documents.new.{dtype}"
    assert not (ROOT / "config.toml").exists()


def test_missing_runtime_config_explains_bootstrap(tmp_path):
    with pytest.raises(FileNotFoundError, match="Copy config.example.toml"):
        Config.load(str(tmp_path / "config.toml"))


def test_routing_templates_require_placeholders():
    data = {
        "service": {},
        "documents": {"host": "localhost", "port": 5672, "exchange": "documents"},
        "events": {"exchange": "events"},
        "routing": {
            "target_key": "actors.static.new.target",
            "document_ingest_key": "documents.ingest.{dtype}",
            "document_update_key": "documents.updated.{dtype}",
            "document_new_key": "documents.new.{dtype}",
            "event_key": "event.{eventType}",
        },
        "actors": {"subfinder": {"enabled": True}},
        "tools": {"subfinder": {"timeout_sec": 30}},
    }

    with pytest.raises(ValueError, match="target_key must contain"):
        Config(data)


def test_pool_sizes_must_be_positive():
    data = {
        "service": {"publisher_pool_size": 0},
        "documents": {"host": "localhost", "port": 5672, "exchange": "documents"},
        "events": {"exchange": "events"},
        "routing": {
            "target_key": "actors.{actor}.new.target",
            "document_ingest_key": "documents.ingest.{dtype}",
            "document_update_key": "documents.updated.{dtype}",
            "document_new_key": "documents.new.{dtype}",
            "event_key": "event.{eventType}",
        },
        "actors": {"subfinder": {"enabled": True}},
        "tools": {"subfinder": {"timeout_sec": 30}},
    }

    with pytest.raises(ValueError, match="publisher_pool_size must be a positive integer"):
        Config(data)
