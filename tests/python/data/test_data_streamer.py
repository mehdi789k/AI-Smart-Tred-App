from __future__ import annotations

from src.python.data.data_streamer import DataStreamer


class DummySocket:
    def __init__(self):
        self.sent = []

    def bind(self, endpoint):
        self.endpoint = endpoint

    def send_string(self, *parts, **kwargs):
        self.sent.append((parts, kwargs))

    def close(self):
        pass


def test_data_streamer_publishes_topic_and_payload(monkeypatch):
    dummy_context = type("Context", (), {"socket": lambda self, kind: DummySocket()})()
    dummy_zmq = type(
        "DummyZMQ",
        (),
        {"Context": type("ContextFactory", (), {"instance": staticmethod(lambda: dummy_context)}), "PUB": 1, "SNDMORE": 2},
    )

    import src.python.data.data_streamer as streamer_module

    monkeypatch.setattr(streamer_module, "zmq", dummy_zmq)
    streamer = DataStreamer(endpoint="inproc://test")

    streamer.publish("EURUSD", "M1", {"close": 1.1234})

    assert streamer._socket is not None
    sent_topic, _ = streamer._socket.sent[0]
    assert sent_topic[0] == "EURUSD_M1"
