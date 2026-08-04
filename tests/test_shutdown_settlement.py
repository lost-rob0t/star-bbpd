import pytest

from bbp.main import shutdown


class FakeActorRef:
    def __init__(self, name, order):
        self.name = name
        self.order = order

    def ask(self, message, *, block, timeout):
        self.order.append((f"{self.name}-intake", block, timeout, type(message).__name__))
        return True

    def stop(self, *, block, timeout):
        self.order.append((self.name, block, timeout))
        return True


class FakePool:
    def __init__(self, name, order):
        self.name = name
        self.order = order

    def stop(self, timeout=None):
        self.order.append((self.name, True, timeout))


class FakePublisher:
    def __init__(self, order):
        self.order = order

    def tell(self, message):
        self.order.append(("shutdown-event", message.routing_key))


class FakeRabbit:
    def __init__(self, order):
        self.order = order

    def close(self):
        self.order.append(("rabbit-close",))


def test_shutdown_keeps_consumers_alive_until_scan_settlement(monkeypatch):
    order = []
    monkeypatch.setattr(
        "bbp.main.pykka.ActorRegistry.stop_all",
        lambda **kwargs: order.append(("registry", kwargs)),
    )
    monkeypatch.setattr(
        "bbp.main.time.sleep",
        lambda *_args: pytest.fail("shutdown must not use fixed sleeps"),
    )

    with pytest.raises(SystemExit):
        shutdown(
            [FakeActorRef("consumer", order)],
            {"tool": FakePool("tool", order)},
            FakePool("publisher", order),
            FakeRabbit(order),
            {"event_key": "event.{eventType}"},
            FakePublisher(order),
            FakeActorRef("dns", order),
            shutdown_timeout=12,
        )

    names = [item[0] for item in order]
    assert names == [
        "shutdown-event",
        "consumer-intake",
        "dns",
        "tool",
        "consumer",
        "publisher",
        "rabbit-close",
        "registry",
    ]
    assert order[1] == ("consumer-intake", True, 12, "StopIntake")
    assert order[3] == ("tool", True, 12)
    assert order[4] == ("consumer", True, 12)
