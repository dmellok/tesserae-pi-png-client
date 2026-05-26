from __future__ import annotations

import json
from typing import Any

from tesserae_pi_png_client.heartbeat import (
    OFFLINE_WILL_PAYLOAD,
    STATUS_TOPIC,
    Heartbeat,
    Status,
    status_summary,
)


class FakePublisher:
    def __init__(self) -> None:
        self.publishes: list[tuple[str, bytes, int, bool]] = []

    def publish(
        self, topic: str, payload: bytes, qos: int = 0, retain: bool = False
    ) -> Any:
        self.publishes.append((topic, payload, qos, retain))
        return None


def test_status_json_round_trips() -> None:
    status = Status(panel="inky_impression_7_3", last_digest="abc123")
    obj = json.loads(status.to_json())
    assert obj["state"] == "idle"
    assert obj["panel"] == "inky_impression_7_3"
    assert obj["last_digest"] == "abc123"
    assert obj["last_paint_at"] is None
    assert obj["last_error"] is None
    assert "uptime_s" in obj
    assert "fw_version" in obj


def test_publish_now_uses_retained_qos1_on_status_topic() -> None:
    status = Status(panel="x")
    publisher = FakePublisher()
    heartbeat = Heartbeat(status=status, publisher=publisher)
    heartbeat.publish_now()
    assert len(publisher.publishes) == 1
    topic, payload, qos, retain = publisher.publishes[0]
    assert topic == STATUS_TOPIC
    assert qos == 1
    assert retain is True
    assert json.loads(payload)["state"] == "idle"


def test_publish_offline_sends_offline_payload_retained() -> None:
    status = Status(panel="x")
    publisher = FakePublisher()
    heartbeat = Heartbeat(status=status, publisher=publisher)
    heartbeat.publish_offline()
    assert len(publisher.publishes) == 1
    topic, payload, qos, retain = publisher.publishes[0]
    assert topic == STATUS_TOPIC
    assert qos == 1
    assert retain is True
    assert payload == OFFLINE_WILL_PAYLOAD
    assert json.loads(payload)["state"] == "offline"


def test_status_summary_is_human_readable() -> None:
    status = Status(panel="x", last_digest="deadbeef")
    summary = status_summary(status)
    assert "state=idle" in summary
    assert "digest=deadbeef" in summary
