from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from bbp.config import Config


class TopologyChannel(Protocol):
    def exchange_declare(
        self, *, exchange: str, exchange_type: str, durable: bool
    ) -> object: ...

    def queue_declare(
        self,
        *,
        queue: str,
        durable: bool,
        exclusive: bool,
        auto_delete: bool,
    ) -> object: ...

    def queue_bind(self, *, queue: str, exchange: str, routing_key: str) -> object: ...


@dataclass(frozen=True, slots=True)
class ExchangeSpec:
    name: str
    exchange_type: str
    durable: bool = True


@dataclass(frozen=True, slots=True)
class QueueBinding:
    queue: str
    exchange: str
    routing_key: str
    durable: bool = True
    exclusive: bool = False
    auto_delete: bool = False


@dataclass(frozen=True, slots=True)
class TopologyPlan:
    exchanges: tuple[ExchangeSpec, ...]
    bindings: tuple[QueueBinding, ...]


def build_topology(config: Config) -> TopologyPlan:
    documents = ExchangeSpec(
        config.documents["exchange"],
        config.documents.get("exchange_type", "topic"),
    )
    events = ExchangeSpec(
        config.events["exchange"],
        config.events.get("exchange_type", "topic"),
    )

    bindings = [
        QueueBinding(
            queue=config.get_actor_config(actor).get(
                "queue", f"bbp.{actor}.targets"
            ),
            exchange=documents.name,
            routing_key=config.routing["target_key"].format(actor=actor),
        )
        for actor in config.enabled_actors()
    ]

    dns = config.data.get("dns_resolver", {})
    if dns.get("enabled", False):
        bindings.append(
            QueueBinding(
                queue=dns.get("queue", "bbp.dns-resolver.domains"),
                exchange=documents.name,
                routing_key=config.routing["document_new_key"].format(
                    dtype="domain"
                ),
            )
        )

    return TopologyPlan(
        exchanges=(documents, events),
        bindings=tuple(bindings),
    )


def declare_topology(channel: TopologyChannel, plan: TopologyPlan) -> None:
    for exchange in plan.exchanges:
        channel.exchange_declare(
            exchange=exchange.name,
            exchange_type=exchange.exchange_type,
            durable=exchange.durable,
        )

    for binding in plan.bindings:
        channel.queue_declare(
            queue=binding.queue,
            durable=binding.durable,
            exclusive=binding.exclusive,
            auto_delete=binding.auto_delete,
        )
        channel.queue_bind(
            queue=binding.queue,
            exchange=binding.exchange,
            routing_key=binding.routing_key,
        )
