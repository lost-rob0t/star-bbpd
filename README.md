# star-bbpd

Python/Pykka external actor service for StarIntel reconnaissance tools.

`star-bbpd` consumes actor-specific target messages from RabbitMQ, runs tools such as Subfinder, Nmap, Httpx, and Katana, and publishes derived StarIntel documents, relations, and actor events.

## Status

The service is being migrated to the current StarIntel 0.9 and star-server contracts. The active migration backlog is tracked in issues #1 through #8.

This branch fixes the first transport/configuration layer:

- runtime `config.toml` is local-only and ignored by Git;
- tracked defaults no longer contain an operational broker hostname or credentials;
- new findings publish to `documents.ingest.<dtype>` for durable server ingestion;
- DNS listens to persisted `documents.new.domain` events and publishes `documents.updated.domain` updates;
- CI provides a stable `Quality gate` check.

The strict 0.9 document migration, publisher-confirmed ACK settlement, and tool-runner overhaul remain open work.

## Requirements

- Python 3.10 or newer
- RabbitMQ
- Subfinder
- Nmap
- Httpx
- Katana

Nix is optional but supported by the repository flake.

## Installation

### Python

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install --editable ".[dev]"
```

### Nix

```bash
nix develop
```

## Configuration

Never commit `config.toml`. Create it from the sanitized example:

```bash
cp config.example.toml config.toml
$EDITOR config.toml
```

The example uses `localhost` and placeholder credentials. Replace them with a dedicated RabbitMQ account before running the service.

### Routing contract

```toml
[routing]
target_key = "actors.{actor}.new.target"
document_ingest_key = "documents.ingest.{dtype}"
document_update_key = "documents.updated.{dtype}"
document_new_key = "documents.new.{dtype}"
event_key = "event.{eventType}"
```

The routes have distinct meanings:

| Key | Purpose |
| --- | --- |
| `actors.<actor>.new.target` | Deliver a persisted target to an external actor |
| `documents.ingest.<dtype>` | Persist a newly derived document or relation |
| `documents.updated.<dtype>` | Persist an update to an existing document |
| `documents.new.<dtype>` | Notify consumers after server persistence |
| `event.<eventType>` | Deliver actor lifecycle and failure events |

Do not publish a new finding directly to `documents.new.*`; that route is event-only and does not perform durable insert by itself.

## Running

```bash
star-bbpd config.toml
```

Or:

```bash
python -m bbp.main config.toml
```

The default path is `config.toml` when no argument is supplied.

## Actors

### Subfinder

- target: domain
- output: domain documents and relations
- command baseline: `subfinder -silent -d <domain>`

### Nmap

- target: host, IP, or CIDR
- output: host documents with service data and relations
- command baseline: `nmap -sV <target>`

### Httpx

- target: host, domain, or URL
- output: URL documents and relations
- command baseline: `httpx -silent -json -u <target>`

### Katana

- target: URL
- output: discovered URL documents and relations
- command baseline: `katana -silent -json -u <url>`

### DNS resolver

The DNS resolver consumes persisted domain events, resolves A and AAAA records, and submits updates through the server update route.

## Development checks

Run the same core checks used by CI:

```bash
python -m compileall -q bbp tests
ruff check bbp tests
pytest --cov=bbp --cov-report=term-missing
python -m build
python -m pip check
```

## CI gate

`.github/workflows/ci.yml` runs on pull requests and pushes to `master`.

It contains:

- **Config safety** — rejects a tracked `config.toml`, missing ignore rule/example, or the retired production broker hostname.
- **Python 3.10 and 3.13** — installs the pinned runtime, compiles, lints, tests, builds, and verifies dependencies.
- **Quality gate** — one stable terminal job intended for branch protection.

Configure the repository ruleset to require the `Quality gate` status before merging to `master`.

## Known blockers

- Strict StarIntel 0.9 boundary migration: #2
- Publisher confirms and correct ACK/NACK settlement: #4
- Deterministic relation identity and recursive provenance: #5
- Timeout-safe runner and parser finalization: #6
- Nix/configuration hardening: #7
- RabbitMQ and star-server integration tests: #8

## License

MIT
