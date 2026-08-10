# star-bbpd

Python/Pykka external actor service for StarIntel reconnaissance tools.

`star-bbpd` consumes actor-specific targets from RabbitMQ, runs Subfinder, Nmap,
Httpx, Katana, and DNS workflows, and publishes derived StarIntel documents,
relations, and actor events.

## Position in the Quasar / StarIntel stack

BBPD is a **runtime service**, not a Quasar browser feature.

```text
quasar-ui
  browser UI / graph renderer / standalone subset
        |
        | typed commands, projections, capability discovery
        v
quasar
  canonical Common Lisp control plane/runtime
        |
        | StarIntel service APIs and adapters
        v
starintel-server
  persistent ingest / storage / search / routing / RabbitMQ
        |
        +-----------------------------+
        |                             |
        v                             v
star-bbpd                       other actor services
  recon tool actors              collectors / analyzers / tools
```

The component responsibilities are deliberately different:

- `quasar-ui` presents controls, graphs, documents, status and results. Its
  standalone browser mode has a bounded browser-safe capability set.
- `quasar` owns the canonical Common Lisp command/control boundary for migrated
  durable Quasar operations, persistent runtime supervision, privileged local
  integrations, reconnect/replay and capability discovery.
- `starintel-server` persists and routes StarIntel documents and provides the
  backend RabbitMQ/CouchDB/HTTP service boundaries assigned to it.
- `star-bbpd` runs external reconnaissance tooling behind the actor-service
  boundary and publishes normalized results back into StarIntel.

A Quasar UI button that submits a BBPD target does **not** make Subfinder, Nmap,
Httpx, Katana or DNS scanning browser capabilities. Connected clients should
discover whether BBPD is available and present that capability accordingly.

BBPD remains independently deployable and supervised. Quasar may expose its
controls, health, logs and results without reimplementing the service in
JavaScript.

## Current migration state

Completed under epic #1:

- strict StarIntel 0.9 document boundary;
- durable new-document routing through `documents.ingest.<dtype>`;
- updates through `documents.updated.<dtype>`;
- centralized durable RabbitMQ topology;
- publisher-confirmed required outputs and settlement-driven ACK/NACK behavior.

Deterministic relation identity and recursive provenance remain issue #5. The timeout-safe subprocess/parser lifecycle remains issue #6.

## StarIntel contract

Production code uses the package-root `starintel_doc` 0.9 API only. The contract is pinned to:

- `starintel-doc`: `980f7a217a0d28f22508c2f05831ceb2fefe8bd0`;
- canonical schema repository: `lost-rob0t/starintel-gpt-auto-dig`;
- canonical schema commit: `ff814ff63868286d68e21502122832802cd5e361`;
- schema version: `0.9.0`;
- schema revision: `0.9.0+fields.20260726.2`.

The boundary rejects unsupported versions, dtype aliases, undeclared fields, wrong types, malformed RFC 3339 timestamps, and malformed BBPD targets. Flat 0.8 input is not silently normalized.

## Message flow

```text
producer
  -> documents.ingest.target
  -> star-server persists target
  -> documents.new.target
  -> star-server target router
  -> actors.<actor>.new.target
  -> star-bbpd tool actor
       -> documents.ingest.domain|host|url|relation
       -> broker publisher confirm
       -> target ACK only after every required publish settles
       -> transient failure: NACK/requeue once
       -> repeated or permanent failure: NACK without requeue
```

DNS follows the same settlement rule:

```text
documents.new.domain
  -> DNS resolver
  -> documents.updated.domain
  -> broker publisher confirm
  -> ACK persisted-domain delivery
```

New findings must never be published directly to `documents.new.*`; that route is for persisted-document events.

## Publisher settlement

Each publisher worker owns exactly one RabbitMQ connection and confirm-enabled channel. No connection or channel crosses actor threads.

Required documents and relations use a synchronous Pykka `ask()` settlement path. A successful result means RabbitMQ confirmed a `mandatory=True` publish. An unroutable publish is categorized as permanent. Broker NACKs, connection loss, and AMQP failures are categorized as transient and retried with bounded backoff.

Lifecycle and diagnostic events are explicitly best-effort. Their failure does not turn a successful scan into a failed target delivery.

### Delivery policy

- Valid target + all required outputs confirmed: ACK.
- Transient scan or publish failure on first delivery: NACK with requeue.
- Transient failure on a RabbitMQ redelivery: NACK without requeue.
- Permanent/unroutable required output: NACK without requeue.
- Invalid JSON/schema/actor target: categorized parse event and ACK to avoid a poison loop.

This bounds a target to two broker deliveries without changing queue arguments. Deterministic retry identities and duplicate-edge prevention are implemented separately in issue #5.

### Shutdown

Shutdown no longer sleeps for fixed intervals. It:

1. stops new target intake while consumer actors remain alive;
2. waits for tool actor mailboxes and required publishes to settle;
3. drains queued ACK/NACK messages;
4. stops publisher workers after their mailboxes drain;
5. closes RabbitMQ connections.

## Configuration

Copy the sanitized example and set local credentials:

```bash
cp config.example.toml config.toml
$EDITOR config.toml
```

Settlement settings:

```toml
[service]
publisher_max_retries = 3
publisher_retry_delays_sec = [1, 2, 4]
publish_settlement_timeout_sec = 30
shutdown_timeout_sec = 60

[tools.subfinder]
publish_timeout_sec = 30
```

The same `publish_timeout_sec` option is available for every tool and the DNS resolver.

Routing templates remain configurable:

```toml
[routing]
target_key = "actors.{actor}.new.target"
document_ingest_key = "documents.ingest.{dtype}"
document_update_key = "documents.updated.{dtype}"
document_new_key = "documents.new.{dtype}"
event_key = "event.{eventType}"
```

## Installation

Python 3.10 or newer is required.

```bash
git clone https://github.com/lost-rob0t/starintel-gpt-auto-dig.git conformance-source
git -C conformance-source checkout ff814ff63868286d68e21502122832802cd5e361
export STARINTEL_CONFORMANCE_ROOT="$PWD/conformance-source"

python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install --editable ".[dev]"
```

For Nix:

```bash
nix develop
```

## Running

```bash
star-bbpd config.toml
```

## Development checks

```bash
python -m compileall -q bbp tests
ruff check bbp tests
pytest --cov=bbp --cov-report=term-missing
python -m build
python -m pip check
```

Run the disposable broker integration tests:

```bash
BBPD_RABBITMQ_INTEGRATION=1 pytest -q tests/test_rabbitmq_routing_integration.py
```

Run Nix validation:

```bash
nix flake check
nix build
```

## Migration loop

- #2: strict StarIntel 0.9 boundary — merged
- #3: durable ingest/update topology — merged
- #4: publisher settlement and ACK/NACK correctness — current change
- #5: deterministic identity and recursive provenance
- #6: tool lifecycle and parser correctness
- #7: remaining configuration/topology/packaging hardening
- #8: complete integration CI

## License

MIT
