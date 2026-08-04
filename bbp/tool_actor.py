#!/usr/bin/env python3

import logging
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import pykka

from bbp.events import create_event
from bbp.publisher_pool import PublishDocument, PublishEvent, PublishRelation

log = logging.getLogger(__name__)


@dataclass
class ScanTarget:
    """Message requesting a tool scan."""

    target: Any
    delivery_tag: int
    consumer_ref: Any


class ToolActor(pykka.ThreadingActor, ABC):
    """Base actor for streaming external-tool scans."""

    def __init__(self, actor_name: str, publisher_ref: Any, tool_config: Dict[str, Any]):
        super().__init__()
        self.actor_name = actor_name
        self.publisher = publisher_ref
        self.tool_config = tool_config
        self.routing: Optional[Dict[str, Any]] = None

    def on_receive(self, message):
        if isinstance(message, ScanTarget):
            self._execute_scan(message.target, message.delivery_tag, message.consumer_ref)
        else:
            log.warning("Unknown message type: %s", type(message))
        return None

    def _execute_scan(self, target: Any, delivery_tag: int, consumer_ref: Any):
        doc_count = 0
        rel_count = 0

        try:
            self._emit_event(
                "scan-start",
                f"Starting {self.actor_name} scan for {target.target}",
                target.id,
            )
            cmd = self._build_command(target)
            log.info("[%s] Executing: %s", self.actor_name, " ".join(cmd))

            timeout = self.tool_config.get("timeout_sec", 300)
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            context: Dict[str, Any] = {}

            try:
                if process.stdout is None:
                    raise RuntimeError("tool stdout pipe was not created")

                for line in iter(process.stdout.readline, ""):
                    if not line:
                        break
                    result = self._parse_line(line, target, context)
                    if not result:
                        continue

                    doc, relation = result
                    if doc:
                        self._emit_document(doc)
                        doc_count += 1
                    if relation:
                        self._emit_relation(relation)
                        rel_count += 1

                try:
                    process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                    stderr = process.stderr.read()[-500:] if process.stderr else ""
                    self._emit_event(
                        "scan-error",
                        f"Timeout after {timeout}s, stderr: {stderr}",
                        target.id,
                    )
                    return

                stderr_output = process.stderr.read() if process.stderr else ""
                if stderr_output:
                    log.warning("[%s] Tool stderr: %s", self.actor_name, stderr_output)

                if process.returncode != 0:
                    self._emit_event(
                        "scan-error",
                        f"Exit code {process.returncode}, stderr: {stderr_output[-500:]}",
                        target.id,
                    )
                else:
                    self._emit_event(
                        "scan-finished",
                        f"Finished {self.actor_name} scan: {doc_count} docs, {rel_count} relations",
                        target.id,
                    )
            except Exception as exc:
                log.error("Error processing tool output: %s", exc, exc_info=True)
                self._emit_event(
                    "scan-error", f"Error processing output: {exc}", target.id
                )
        except Exception as exc:
            log.error("Error executing scan: %s", exc, exc_info=True)
            self._emit_event("scan-error", f"Error executing tool: {exc}", target.id)
        finally:
            self._send_ack(consumer_ref, delivery_tag)

    def _send_ack(self, consumer_ref: Any, delivery_tag: int):
        from bbp.consumer import AckMessage

        consumer_ref.tell(AckMessage(delivery_tag))

    @abstractmethod
    def _build_command(self, target: Any) -> List[str]:
        raise NotImplementedError

    @abstractmethod
    def _parse_line(
        self, line: str, target: Any, context: Dict[str, Any]
    ) -> Optional[Tuple[Any, Any]]:
        raise NotImplementedError

    def _require_routing(self) -> Dict[str, Any]:
        if not self.routing:
            raise RuntimeError(f"[{self.actor_name}] routing config is unavailable")
        return self.routing

    def _emit_document(self, doc: Any):
        routing = self._require_routing()
        routing_key = routing["document_ingest_key"].format(dtype=doc.dtype)
        self.publisher.tell(PublishDocument(doc, routing_key))

    def _emit_relation(self, rel: Any):
        routing = self._require_routing()
        routing_key = routing["document_ingest_key"].format(dtype="relation")
        self.publisher.tell(PublishRelation(rel, routing_key))

    def _emit_event(self, event_type: str, details: str, source_id: str):
        routing = self._require_routing()
        event = create_event(self.actor_name, event_type, details, source_id)
        routing_key = routing["event_key"].format(eventType=event_type)
        self.publisher.tell(PublishEvent(event, routing_key))
