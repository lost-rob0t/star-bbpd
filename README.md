# star-bbpd

BBP Actor Service - A Python actor-based service using Pykka and pika for bug bounty reconnaissance tool orchestration.

## Overview

The BBP Actor Service runs bug bounty and reconnaissance tools (subfinder, nmap, httpx, katana) outside of gserver in a separate Python process. It consumes StarIntel Target documents from RabbitMQ, executes the appropriate tool, and emits StarIntel documents and relations back to RabbitMQ for gserver ingestion.

**Key Features:**
- Actor-based architecture using Pykka ThreadingActor
- RabbitMQ integration with manual ack for reliability
- Line-by-line output processing for memory safety
- Automatic dataset and sources propagation
- Event subsystem integration for monitoring
- Graceful shutdown with in-flight scan completion

## Architecture

### Core Components

- **RabbitManager**: Connection and channel management with exponential backoff
- **Publisher**: Publishes all documents, relations, and events to RabbitMQ
- **TargetConsumer** (one per tool): Consumes targets from actor-specific queues
- **ToolActor** (abstract base): Executes tool subprocess, parses output, emits docs/relations/events
- **Tool-specific actors**: SubfinderActor, NmapActor, HttpxActor, KatanaActor

### Message Flow

```
RabbitMQ (actors.subfinder.new.target)
  → TargetConsumer validates & forwards
    → ToolActor executes subprocess
      → Parses output line-by-line
        → Publisher emits to documents exchange
          → TargetConsumer acks after completion
```

## Prerequisites

### System Requirements

- Python 3.9 or higher
- RabbitMQ server (local or remote)
- Nix (optional, but recommended for reproducible builds)

### External Tools

The following tools must be installed and available in PATH:

- **subfinder**: Subdomain enumeration tool
- **nmap**: Network scanner
- **httpx**: HTTP toolkit
- **katana**: Web crawler

Install these tools separately or ensure they're available in your environment.

## Installation

### Using Nix (Recommended)

```bash
# Enter development environment
nix develop

# Or build the package
nix build

# Or run directly
nix run
```

### Using pip

```bash
# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -e .
```

## Configuration

Configuration is managed via TOML files. See `config.toml` for the full configuration example.

### Configuration Sections

#### [service]
```toml
[service]
name = "bbp-actors"
log_level = "INFO"           # DEBUG, INFO, WARNING, ERROR
json_pretty = false
```

#### [documents]
RabbitMQ connection settings for the documents exchange:

```toml
[documents]
host = "localhost"
port = 5672
vhost = "/"
username = "guest"
password = "guest"
exchange = "documents"
exchange_type = "topic"
heartbeat_sec = 30
```

#### [events]
Events exchange configuration:

```toml
[events]
exchange = "events"
exchange_type = "topic"
```

#### [routing]
Routing key templates:

```toml
[routing]
target_key = "actors.{actor}.new.target"
doc_key = "document.new.{dtype}"
event_key = "event.{eventType}"
```

#### [actors.*]
Per-actor configuration:

```toml
[actors.subfinder]
enabled = true
queue = "bbp.subfinder.targets"
prefetch = 1                  # Number of messages to prefetch
emit_relations = true
```

#### [tools.*]
Tool-specific settings:

```toml
[tools.subfinder]
bin = "subfinder"             # Path to binary (or name if in PATH)
timeout_sec = 300             # Execution timeout in seconds
args = ["-silent"]            # Additional command arguments
```

## Usage

### Start the Service

```bash
# Using Nix
nix run

# Or directly with Python
python bbp/main.py

# Or with custom config
python bbp/main.py /path/to/config.toml
```

### Testing with RabbitMQ

1. Start RabbitMQ:
```bash
docker run -d -p 5672:5672 -p 15672:15672 rabbitmq:3-management
```

2. Create a test target (Python):
```python
import sys
sys.path.insert(0, "/path/to/starintel_doc")
from starintel_doc.targets import new_target
import pika

# Create target
target = new_target("test-dataset", "example.com", "subfinder")

# Publish to RabbitMQ
connection = pika.BlockingConnection(pika.ConnectionParameters('localhost'))
channel = connection.channel()
channel.basic_publish(
    exchange='documents',
    routing_key='actors.subfinder.new.target',
    body=target.to_json()
)
print(f"Published target: {target.to_json()}")
connection.close()
```

3. Monitor output on RabbitMQ management console:
   - Navigate to http://localhost:15672 (guest/guest)
   - Check exchanges: `documents` and `events`
   - View queues: `bbp.subfinder.targets`

### Graceful Shutdown

Press `Ctrl+C` (SIGINT) or send SIGTERM to gracefully shutdown:

1. Stops accepting new targets
2. Waits for in-flight scans to complete
3. Flushes pending publishes
4. Closes RabbitMQ connections
5. Shuts down actor system

## Tool Actors

### Subfinder
- **Consumes**: Domain targets
- **Emits**: Domain documents
- **Relations**: `target → domain` (predicate: `has-a`, note: `subdomain`)
- **Command**: `subfinder -silent -d <domain>`

### Nmap
- **Consumes**: Host/IP/CIDR targets
- **Emits**: Host documents with service/port information
- **Relations**: `target → host` (predicate: `related-to`, note: `host-discovery`)
- **Command**: `nmap -sV <target>`

### Httpx
- **Consumes**: Host/domain/URL targets
- **Emits**: URL documents
- **Relations**: `target → url` (predicate: `related-to`, note: `httpx`)
- **Command**: `httpx -silent -json -u <target>`

### Katana
- **Consumes**: URL targets
- **Emits**: URL documents (discovered URLs)
- **Relations**: `seed → discovered` (predicate: `links-to`, note: `crawl`)
- **Command**: `katana -silent -json -u <url>`

## Event Types

The service emits events to the `events` exchange with routing key `event.{eventType}`:

- **service-start**: Service starting up
- **service-ready**: Service ready to accept targets
- **service-shutdown**: Service shutting down
- **scan-start**: Tool scan starting
- **scan-finished**: Tool scan completed successfully
- **scan-error**: Tool scan failed or timed out
- **publish-error**: Failed to publish document/relation
- **parse-error**: Failed to parse target JSON

## Development

### Project Structure

```
bbp/
├── __init__.py
├── main.py                  # Orchestration + signal handling
├── config.py                # TOML loading + validation
├── rabbit.py                # RabbitManager (connection + channels)
├── publisher.py             # Publisher actor (docs/relations/events)
├── consumer.py              # TargetConsumer actor (RabbitMQ consumption)
├── tool_actor.py            # Abstract ToolActor base class
├── events.py                # Event creation helpers
├── util.py                  # Dataset/sources propagation
└── tools/
    ├── __init__.py          # TOOL_CLASSES export
    ├── subfinder.py         # SubfinderActor
    ├── nmap.py              # NmapActor
    ├── httpx.py             # HttpxActor
    └── katana.py            # KatanaActor
```

### Development Environment

```bash
# Enter development environment
nix develop

# Format code
black .

# Type checking
mypy bbp/

# Linting
flake8 bbp/
```

## Flake Outputs

This project provides the following Nix flake outputs:

### Packages
- `packages.default` - The star-bbpd package (default)
- `packages.star-bbpd` - The star-bbpd package explicitly

### Development Shells
- `devShells.default` - Development environment with:
  - Python 3
  - pika (RabbitMQ client library)
  - pykka (Actor framework)
  - ulid (ULID generation)
  - tomli (TOML parsing)
  - pytest (testing framework)
  - black (code formatter)
  - flake8 (linter)
  - mypy (type checker)

### Apps
- `apps.default` - Runs the star-bbpd application

## Troubleshooting

### RabbitMQ Connection Failed

- Check RabbitMQ is running: `docker ps` or `systemctl status rabbitmq-server`
- Verify connection settings in config.toml
- Check network connectivity to RabbitMQ host

### Tool Not Found

- Ensure tool binaries (subfinder, nmap, httpx, katana) are in PATH
- Or provide absolute path in `[tools.*]` section: `bin = "/usr/local/bin/subfinder"`

### Parse Errors

- Check target JSON format matches StarIntel Target schema
- Verify `dtype == "target"` and `actor` field matches expected actor name
- Enable DEBUG logging to see detailed parse errors

### Memory Issues

- The service processes tool output line-by-line for memory safety
- Adjust `prefetch` values in actor configs to limit concurrent scans
- Monitor system resources during large scans

## License

MIT

## References

- Specification: `BBP_ACTOR_SERVICE.org`
- StarIntel Document Library: `../spec/starintel_doc`
- StarIntel Server: `../starintel-server`
