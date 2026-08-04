from bbp.consumer import TargetConsumer


class FakeChannel:
    def __init__(self):
        self.is_open = True
        self.acks = []
        self.nacks = []

    def basic_ack(self, **kwargs):
        self.acks.append(kwargs)

    def basic_nack(self, **kwargs):
        self.nacks.append(kwargs)


class FakeConnection:
    is_open = True

    def add_callback_threadsafe(self, callback):
        callback()


class FakePublisher:
    def tell(self, message):
        del message


class FakeTool:
    def tell(self, message):
        del message


def consumer():
    value = TargetConsumer(
        "subfinder",
        {},
        FakePublisher(),
        FakeTool(),
        {},
        {"event_key": "event.{eventType}"},
    )
    value.connection = FakeConnection()
    value.channel = FakeChannel()
    return value


def test_ack_is_scheduled_on_consumer_connection_thread():
    value = consumer()
    value._handle_ack(7)
    assert value.channel.acks == [{"delivery_tag": 7}]


def test_nack_requeue_is_scheduled_on_consumer_connection_thread():
    value = consumer()
    value._handle_nack(8, True)
    assert value.channel.nacks == [{"delivery_tag": 8, "requeue": True}]


def test_nack_reject_is_scheduled_on_consumer_connection_thread():
    value = consumer()
    value._handle_nack(9, False)
    assert value.channel.nacks == [{"delivery_tag": 9, "requeue": False}]


class FakeLiveThread:
    def __init__(self):
        self.join_calls = []

    def is_alive(self):
        return True

    def join(self, timeout=None):
        self.join_calls.append(timeout)


def test_stop_intake_preserves_connection_for_late_settlement():
    value = consumer()
    value.consume_thread = FakeLiveThread()
    value._intake_stopped.set()

    assert value._stop_intake() is True
    assert value.connection.is_open is True
    assert value.consume_thread.join_calls == []
