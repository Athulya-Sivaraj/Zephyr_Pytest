"""Host-side MQTT client used to verify what the DUT publishes and to publish to it.

Optional: needs `pip install paho-mqtt` (v1 or v2). Tests that need it are skipped
when it is missing.
"""
from __future__ import annotations

import threading
import uuid


class MqttPeer:
    def __init__(self, host: str, port: int):
        import paho.mqtt.client as mqtt   # imported lazily so the suite runs without paho

        kwargs = {}
        if hasattr(mqtt, 'CallbackAPIVersion'):          # paho-mqtt >= 2.0
            kwargs['callback_api_version'] = mqtt.CallbackAPIVersion.VERSION2
        self._client = mqtt.Client(client_id=f'pnet-host-{uuid.uuid4().hex[:6]}', **kwargs)
        self._client.on_message = self._on_message
        self._cond = threading.Condition()
        self.messages: list[tuple[str, bytes, int]] = []
        self._client.connect(host, port, keepalive=30)
        self._client.loop_start()

    def _on_message(self, _client, _userdata, msg):
        with self._cond:
            self.messages.append((msg.topic, msg.payload, msg.qos))
            self._cond.notify_all()

    def subscribe(self, topic: str, qos: int = 1) -> None:
        self._client.subscribe(topic, qos)

    def publish(self, topic: str, payload: str, qos: int = 1, retain: bool = False) -> None:
        self._client.publish(topic, payload, qos=qos, retain=retain).wait_for_publish(10)

    def wait_message(self, topic: str, payload: str, timeout: float = 15) -> bool:
        with self._cond:
            return self._cond.wait_for(
                lambda: any(t == topic and p == payload.encode() for t, p, _ in self.messages), timeout)

    def count(self, topic: str) -> int:
        with self._cond:
            return sum(1 for t, _, _ in self.messages if t == topic)

    def clear(self) -> None:
        with self._cond:
            self.messages.clear()

    def close(self) -> None:
        self._client.loop_stop()
        self._client.disconnect()
