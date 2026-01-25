# AGENTS.md
# BBP Ralph Constraints (Rules + Target End-State)

## Primary Objective
Make the publishing path **provably thread-safe** and **interface-stable**.

## Hard Constraints
- Do NOT change StarIntel document/event schema.
- Do NOT change dataset propagation rules.
- Do NOT change routing key templates (must stay configurable via TOML).
- Do NOT add new dependencies unless absolutely necessary (expect: none).
- Do NOT do formatting-only refactors.
- Do NOT rename modules/symbols unless required to satisfy thread-safety + stability.

## Thread-Safety Invariants (must hold after every iteration)
- pika `BlockingConnection` objects are **owned by exactly one thread**.
- pika `Channel` objects are **owned by exactly one thread**.
- No module exposes a shared pika channel for multi-thread use.
- No cross-thread calls touch pika channel methods directly.
- Publishing is done via **message passing** into a publisher worker that owns its pika resources.

## Publisher End-State Requirements
- There is **one** authoritative publishing interface used everywhere.
- Tool execution code never depends on publisher internals.
- The publisher interface supports:
  - publish_document(doc, routing_key)
  - publish_relation(rel, routing_key)
  - publish_event(event, routing_key)
  - (or a single `.tell(PublishX(...))` API — but it must be consistent + documented)
- Worker selection, round-robin, retries, reconnects are **publisher-private** implementation details.

## Allowed Changes
Only make changes in these areas:
- Publisher layer implementation (pool/workers, retry logic, reconnect logic)
- RabbitMQ connection/channel creation boundaries (thread ownership)
- Wiring / dependency injection so everyone uses the same publisher API
- Minimal safety asserts + runtime checks that prevent unsafe wiring

## Forbidden Changes
- No “general cleanup”, no reformatting, no reorganizing directories.
- No feature expansion (no new events, no new predicates, no new docs).
- No changes to tool parsing logic unless required for publisher API integration.
- No changes to ack policy unless required to preserve correctness.

## Iteration Protocol (Ralph loop discipline)
Each iteration must:
1) Pick ONE narrowly-scoped task.
2) Implement it with minimal diff.
3) Add/adjust asserts to enforce invariants (fail fast).
4) Update `progress.txt` with:
   - what changed
   - what invariant is now enforced
   - what the next single task is
5) Produce ONE commit.

## Commit Rules
- One commit per iteration.
- Commit message format:
  - `fix(publisher): ...`
  - `fix(rabbit): ...`
  - `fix(wiring): ...`
  - `chore(asserts): ...`

## Quality Gates (must pass)
- Service starts without runtime type/wiring hacks.
- Publishing works under concurrent tool activity (no shared pika usage).
- Shutdown remains graceful (best-effort flush, no deadlocks).
- Errors are surfaced as events (best-effort) and do not poison-loop.

## Assert Policy (fail-fast)
Add explicit runtime asserts where it prevents unsafe wiring:
- Assert publisher instance conforms to the chosen stable API.
- Assert no code path calls into publisher-private internals.
- Assert connections/channels are created per-thread (or per-worker) only.

## Test file policy
when you are doing a small test (not unit testing) write the temp test files to /tmp/w
