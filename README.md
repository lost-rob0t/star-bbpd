# star-bbpd

Python/Pykka external actor service for StarIntel reconnaissance tools.

`star-bbpd` consumes actor-specific targets from RabbitMQ, runs Subfinder, Nmap, Httpx, and Katana, and publishes derived StarIntel documents, relations, and actor events.

## Status

The service is migrating through epic #1 in dependency order. The current implementation provides:

- a strict StarIntel 0.9 document boundary;
- validated inbound target and domain messages;
- canonical 0.9 domain, host, URL, target, and relation builders;
- durable new-document routing through `documents.ingest.<dtype>`;
- domain updates through `documents.updated.domain`;
- one centralized durable RabbitMQ topology plan;
- local-only runtime configuration and a stable CI quality gate.

Publisher-confirmed ACK settlement, deterministic relation identity, recursive provenance, and the tool-runner lifecycle remain separate follow-up issues.

## StarIntel contract

Production code imports the package-root `starintel_doc` 0.9 API only. Legacy flat modules such as `starintel_doc.targets`, `starintel_doc.hosts`, and `starintel_doc.relations` are not used.

The contract is pinned to:

- `starintel-doc`: `980f7a217a0d28f22508c2f05831ceb2fefe8bd0`;
- canonical schema repository: `lost-rob0t/starintel-gpt-auto-dig`;
- canonical schema commit: `ff814ff63868286d68e21502122832802cd5e361`;
- schema version: `0.9.0`;
- schema revision: `0.9.0+fields.20260726.2`.

The boundary rejects unsupported schema versions, dtype aliases, undeclared fields, wrong types, malformed RFC 3339 timestamps, and missing BBPD target fields. It does not silently normalize flat 0.8 input.

Nmap service observations are embedded values in `host.data.ports` using `port`, `name`, and `version`. They are not standalone document types.

The migration preserves the previous deterministic domain, host, URL, and target deduplication keys for now. Relation identity and the move to the approved deterministic identity design belong to issue #5.

## Requirements

- Python 3.10 or newer
- RabbitMQ
- Subfinder
- Nmap
- Httpx
- Katana

## Installation

### Python

Check out the pinned schema source, then expose its root to the adapter:

```bash
git clone https://github.com/lost-rob0t/starintel-gpt-auto-dig.git conformance-source
git -C conformance-source checkout ff814ff63868286d68e21502122832802cd5e361
export STARINTEL_CONFORMANCE_ROOT="$PWD/conformance-source"

python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install --editable ".[dev]"
```

`STARINTEL_SCHEMA` may instead point directly to `starintel-doc-v0.9.0.schema.json`. The matching manifest must be beside it.

### Nix

```bash
nix develop
```

The flake fetches the exact package and schema commits and sets `STARINTEL_CONFORMANCE_ROOT` automatically.

## Configuration

Never commit `config.toml`. Create it from the sanitized example:

```bash
cp config.example.toml config.toml
$EDITOR config.toml
```

### Routing contract

```toml
[routing]
target_key = "actors.{actor}.new.target"
document_ingest_key = "documents.ingest.{dtype}"
document_update_key = "documents.updated.{dtype}"
document_new_key = "documents.new.{dtype}"
event_key = "event.{eventType}"
```

| Key | Purpose |
| --- | --- |
| `actors.<actor>.new.target` | Deliver a persisted target to an external actor |
| `documents.ingest.<dtype>` | Persist a newly derived document or relation |
| `documents.updated.<dtype>` | Persist an update to an existing document |
| `documents.new.<dtype>` | Notify consumers after server persistence |
| `event.<eventType>` | Deliver actor lifecycle and failure events |

### Message flow

```text
producer
  -> documents.ingest.target
  -> star-server persists target in CouchDB
  -> documents.new.target
  -> star-server target router
  -> actors.<actor>.new.target
  -> star-bbpd tool actor
       -> documents.ingest.domain|host|url|relation
       -> star-server persists finding
       -> documents.new.<dtype> with _id and _rev
            -> DNS resolver consumes documents.new.domain
            -> documents.updated.domain
            -> star-server updates the existing CouchDB document
```

New findings must never be published directly to `documents.new.*`; that route is for persisted-document events. BBPD derives all exchanges, actor target queues, and the DNS persisted-domain binding from `bbp.topology` and validated configuration.

Document and relation publishes use persistent JSON messages with UTF-8 content encoding and AMQP `type` equal to the canonical document dtype.

## Running

```bash
star-bbpd config.toml
```

Or:

```bash
python -m bbp.main config.toml
```

## Actors

- **Subfinder:** domain targets to domain documents and relations.
- **Nmap:** host/IP/CIDR targets to host documents with embedded port/service values and relations.
- **Httpx:** host/domain/URL targets to URL documents and relations.
- **Katana:** URL targets to discovered URL documents and relations.
- **DNS resolver:** persisted domain events to validated domain updates.

## Development checks

```bash
python -m compileall -q bbp tests
ruff check bbp tests
pytest --cov=bbp --cov-report=term-missing
python -m build
python -m pip check
```

Run the broker routing integration test against a disposable RabbitMQ instance:

```bash
BBPD_RABBITMQ_INTEGRATION=1 pytest -q tests/test_rabbitmq_routing_integration.py
```

Run the pinned upstream fixture suite:

```bash
(
  cd "$STARINTEL_CONFORMANCE_ROOT"
  python -m conformance.runner test \
    --producer python \
    --consumer python \
    --adapter "python=sh -c 'cd $OLDPWD && python -m starintel_doc.conformance_adapter'"
)
```

Run Nix validation:

```bash
nix flake check
nix build
```

## Migration loop

- #2: strict StarIntel 0.9 boundary — merged
- #3: durable ingest/update topology — current change
- #4: publisher settlement and ACK/NACK correctness
- #5: deterministic identity and recursive provenance
- #6: tool lifecycle and parser correctness
- #7: remaining configuration/topology/packaging hardening
- #8: complete integration CI

## License

MIT
