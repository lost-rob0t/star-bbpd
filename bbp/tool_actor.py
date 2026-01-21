#!/usr/bin/env python3

import logging
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import pykka

from bbp.events import create_event
from bbp.publisher_pool import PublishDocument, PublishRelation, PublishEvent
from bbp.util import propagate_sources, propagate_dataset

log = logging.getLogger(__name__)


@dataclass
class ScanTarget:
    """Message to scan a target"""

    target: Any  # StarIntel Target object
    delivery_tag: int  # For ack callback
    consumer_ref: Any  # Reference to TargetConsumer for ack


class ToolActor(pykka.ThreadingActor, ABC):
    """
    Abstract base class for tool actors.

    Executes tool subprocess, parses output line-by-line,
    emits documents/relations/events, and enforces dataset/sources propagation.

    Subclasses must implement:
        - _build_command(target): Build command list
        - _parse_line(line, target, context): Parse tool output line
    """

    def __init__(self, actor_name: str, publisher_ref: Any, tool_config: Dict[str, Any]):
        super().__init__()
        self.actor_name = actor_name
        self.publisher = publisher_ref
        self.tool_config = tool_config
        self.routing = None  # Set by main during initialization

    def on_receive(self, message):
        """Handle incoming messages"""
        if isinstance(message, ScanTarget):
            self._execute_scan(message.target, message.delivery_tag, message.consumer_ref)
        else:
            log.warning(f"Unknown message type: {type(message)}")

        return None

    def _execute_scan(self, target: Any, delivery_tag: int, consumer_ref: Any):
        """
        Execute tool scan for the given target.

        Args:
            target: StarIntel Target object
            delivery_tag: For ack callback
            consumer_ref: Reference to TargetConsumer actor for ack
        """
        doc_count = 0
        rel_count = 0

        try:
            # Emit scan-start event
            self._emit_event("scan-start", f"Starting {self.actor_name} scan for {target.target}", target.id)

            # Build command
            cmd = self._build_command(target)
            log.info(f"[{self.actor_name}] Executing: {' '.join(cmd)}")

            # Execute subprocess with timeout
            timeout = self.tool_config.get("timeout_sec", 300)
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            # Context dict for stateful parsing (e.g., nmap)
            context: Dict[str, Any] = {}

            # Log stderr output for debugging
            stderr_lines = []
            
            # Process output line-by-line (memory safe)
            try:
                for line in iter(process.stdout.readline, ""):
                    if not line:
                        break

                    # Parse line (may return doc, relation, or None)
                    result = self._parse_line(line, target, context)

                    if result:
                        doc, relation = result

                        # Emit document if present
                        if doc:
                            self._emit_document(doc)
                            doc_count += 1
                            log.info(f"[{self.actor_name}] Created document {doc_count}: {doc.dtype} ({doc.id})")

                        # Emit relation if present
                        if relation:
                            self._emit_relation(relation)
                            rel_count += 1
                            log.info(f"[{self.actor_name}] Created relation {rel_count}: {relation.predicate} ({relation.source} -> {relation.target})")
                            log.info(f"[{self.actor_name}] Created relation {rel_count}: {relation.predicate} ({relation.source} -> {relation.target})")

                # Wait for completion with timeout
                try:
                    process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    process.kill()
                    stderr = process.stderr.read()[-500:] if process.stderr else ""
                    self._emit_event(
                        "scan-error",
                        f"Timeout after {timeout}s, stderr: {stderr}",
                        target.id,
                    )
                    # Still ack the message
                    self._send_ack(consumer_ref, delivery_tag)
                    return

                # Read stderr output for debugging
                stderr_output = ""
                if process.stderr:
                    stderr_output = process.stderr.read()
                    if stderr_output:
                        log.warning(f"[{self.actor_name}] Tool stderr output: {stderr_output}")

                # Check exit code
                exit_code = process.returncode
                if exit_code != 0:
                    stderr_snippet = stderr_output[-500:] if stderr_output else ""
                    self._emit_event(
                        "scan-error",
                        f"Exit code {exit_code}, stderr: {stderr_snippet}",
                        target.id,
                    )
                else:
                    # Success
                    log.info(f"[{self.actor_name}] Scan completed successfully: {doc_count} documents, {rel_count} relations created")
                    self._emit_event(
                        "scan-finished",
                        f"Finished {self.actor_name} scan: {doc_count} docs, {rel_count} relations",
                        target.id,
                    )

            except Exception as e:
                log.error(f"Error processing tool output: {e}", exc_info=True)
                self._emit_event(
                    "scan-error",
                    f"Error processing output: {str(e)}",
                    target.id,
                )

        except Exception as e:
            log.error(f"Error executing scan: {e}", exc_info=True)
            self._emit_event(
                "scan-error",
                f"Error executing tool: {str(e)}",
                target.id,
            )

        finally:
            # Always ack the message
            self._send_ack(consumer_ref, delivery_tag)

    def _send_ack(self, consumer_ref: Any, delivery_tag: int):
        """Send ack message to consumer"""
        from bbp.consumer import AckMessage
        consumer_ref.tell(AckMessage(delivery_tag))

    @abstractmethod
    def _build_command(self, target: Any) -> List[str]:
        """
        Build command to execute.

        Args:
            target: StarIntel Target object

        Returns:
            List of command arguments (e.g., ["subfinder", "-silent", "-d", "example.com"])
        """
        pass

    @abstractmethod
    def _parse_line(
        self, line: str, target: Any, context: Dict[str, Any]
    ) -> Optional[Tuple[Any, Any]]:
        """
        Parse a line of tool output.

        Args:
            line: Line from tool stdout
            target: StarIntel Target object
            context: Shared context dict for stateful parsing

        Returns:
            (document, relation) tuple, or None if line should be skipped
            Either doc or relation can be None
        """
        pass

    def _emit_document(self, doc: Any):
        """
        Emit a document to the publisher.

        Args:
            doc: StarIntel document object
        """
        routing_key = self.routing["doc_key"].format(dtype=doc.dtype)
        self.publisher.tell(PublishDocument(doc, routing_key))
        log.debug(f"[{self.actor_name}] Emitted {doc.dtype} document: {doc.id}")

    def _emit_relation(self, rel: Any):
        """
        Emit a relation to the publisher.

        Args:
            rel: StarIntel Relation object
        """
        routing_key = self.routing["doc_key"].format(dtype="relation")
        self.publisher.tell(PublishRelation(rel, routing_key))
        log.debug(
            f"[{self.actor_name}] Emitted relation: {rel.predicate} ({rel.source} -> {rel.target})"
        )

    def _emit_event(self, event_type: str, details: str, source_id: str):
        """
        Emit an event to the publisher.

        Args:
            event_type: Type of event (scan-start, scan-finished, scan-error, etc.)
            details: Freeform details string
            source_id: ID of the source document (usually target._id)
        """
        event = create_event(self.actor_name, event_type, details, source_id)
        routing_key = self.routing["event_key"].format(eventType=event_type)
        self.publisher.tell(PublishEvent(event, routing_key))
        log.debug(f"[{self.actor_name}] Emitted event: {event_type}")
