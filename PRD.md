# PRD.md

# BBP Actor Service — Publisher is busted (thread safety) + Fix Plan

## Problem Statement (authoritative)
The current publishing path is **not thread-safe** and is **functionally busted**.

Evidence from current code:
- `RabbitManager.connect()` creates one `pika.BlockingConnection` stored on `self.connection`.
- `RabbitManager.get_publish_channel()` returns a channel derived from that shared connection and caches it in `_publish_channel`.
- Multiple Pykka actors / threads call publish concurrently through this shared channel (directly or indirectly).
- Pika `BlockingConnection` and its channels are **not thread-safe**. Sharing them across threads will eventually explode (random StreamLostError, deque pops, channel closed, interleaved frames, etc).

Therefore:
- Any design that reuses a single connection/channel across multiple publisher workers or tool actors is invalid.
- Publishing must use **one connection per thread** (or single-threaded publisher with a queue, but that’s not the architecture here).

## What “fixed” means
- Every publisher worker owns:
  - its own `pika.BlockingConnection`
  - its own `channel`
  - no channel is ever used from a different thread
- Tool actors may publish concurrently, but they must publish by sending messages to publisher worker actors, never by touching pika objects directly.

## Hard Constraints
- No StarIntel spec changes.
- No schema changes.
- Keep TOML config.
- Single process. Pykka ThreadingActor stays.
- No direct DB writes, only RabbitMQ.
- Fail fast if config invalid.

## Required Refactor (one task per Ralph iteration)
### Task A — Single publisher implementation
- Delete or fully deprecate `bbp/publisher.py` (single Publisher actor) OR ensure it’s never used.
- Keep only `bbp/publisher_pool.py` as the authoritative publisher layer.
- All code should depend on the same stable interface.

### Task B — Fix RabbitManager threading violations
- RabbitManager must NOT expose a shared publish channel for multi-threaded use.
- Keep `RabbitManager.new_connection()` as the only supported connection factory.
- Remove or lock down `get_publish_channel()` to single-thread contexts only OR eliminate it.
- Topology declaration may use a short-lived connection/channel, but publishing cannot.

### Task C — ToolActor publisher integration is wrong
Current ToolActor does:
- assigns `self._assigned_worker = self.publisher._get_next_worker()` and uses it directly

That is busted because:
- ToolActor assumes publisher has internal method `_get_next_worker()` (pool internals leak)
- Publisher might be proxy or different type depending on wiring

Fix:
- Provide a stable API on publisher proxy for:
  - publish_document(doc, routing_key)
  - publish_relation(rel, routing_key)
  - publish_event(event, routing_key)
- ToolActor must call ONLY those methods (or send Publish* messages to a stable actor ref).
- No direct access to worker selection internals from ToolActor.

### Task D — Consumer / ack behavior stays
- Ack is sent only after scan attempt finishes.
- Parse-error emits event and acks immediately.
- No change required unless thread safety issues appear.

### Task E — Service wiring in main.py is inconsistent
- `publisher_pool.get_proxy()` returns a proxy-like object with `.tell()` and `.stop()`
- But `main.py` also stores `publisher_ref = publisher_pool` (instance), passes that around.
- Standardize:
  - Pass `publisher_pool_proxy` everywhere for publishing calls
  - Keep `publisher_pool` instance only for shutdown (stop workers)

### Definition of Done
- No shared pika channel across threads. Ever.
- Publisher path is uniform and boring:
  - ToolActor -> PublisherPoolProxy -> PublisherWorker -> pika connection/channel owned by that worker thread.
- Subfinder/httpx/katana/nmap can publish concurrently without random pika failures.
- Service starts, runs, publishes, shuts down cleanly.

## Safety rails for Ralph
- Each iteration:
  - Do one minimal task from A–E
  - Make a small commit
  - Append to progress.txt what changed + next step
- If a change requires broad edits, split it into multiple iterations.
