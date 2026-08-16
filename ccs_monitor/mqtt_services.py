from __future__ import annotations

import asyncio
import threading
from typing import Any

from PySide6.QtCore import QObject, Signal, Slot

from .mqtt_config import MqttMonitoringConfig


class MqttBrokerService(QObject):
    status_changed = Signal(str, bool)

    def __init__(self, config: MqttMonitoringConfig, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.config = config
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event: asyncio.Event | None = None
        self._stop_requested = threading.Event()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_requested.clear()
        self._thread = threading.Thread(target=self._thread_main, name="ccs-mqtt-broker", daemon=True)
        self._thread.start()

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._run_broker())
        except Exception as exc:
            self.status_changed.emit(f"MQTT Broker 启动失败：{exc}", False)
        finally:
            self._loop = None
            self._stop_event = None

    async def _run_broker(self) -> None:
        from amqtt.broker import Broker

        self._loop = asyncio.get_running_loop()
        self._stop_event = asyncio.Event()
        broker_config = {
            "listeners": {
                "default": {
                    "type": "tcp",
                    "bind": f"{self.config.bind_host}:{self.config.port}",
                }
            },
            "plugins": {
                "amqtt.plugins.authentication.AnonymousAuthPlugin": {
                    "allow_anonymous": True,
                },
            },
        }
        broker = Broker(broker_config)
        try:
            await broker.start()
            self.status_changed.emit(f"MQTT Broker 运行中 · TCP {self.config.port}", True)
            if self._stop_requested.is_set():
                self._stop_event.set()
            await self._stop_event.wait()
        finally:
            await broker.shutdown()
            self.status_changed.emit("MQTT Broker 已停止", False)

    def stop(self, timeout_seconds: float = 5.0) -> None:
        self._stop_requested.set()
        if self._loop and not self._loop.is_closed() and self._stop_event:
            self._loop.call_soon_threadsafe(self._stop_event.set)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout_seconds)


class MqttSubscriber(QObject):
    message_received = Signal(str, bytes)
    status_changed = Signal(str, bool)

    def __init__(self, config: MqttMonitoringConfig, client_factory=None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.config = config
        self._client_factory = client_factory
        self._client: Any | None = None
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        import paho.mqtt.client as mqtt

        factory = self._client_factory or mqtt.Client
        try:
            client = factory(
                callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                client_id=self.config.subscriber_client_id,
                protocol=mqtt.MQTTv311,
            )
        except TypeError:
            client = factory(client_id=self.config.subscriber_client_id, protocol=mqtt.MQTTv311)
        self._client = client
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_connect_fail = self._on_connect_fail
        client.on_message = self._on_message
        client.reconnect_delay_set(min_delay=1, max_delay=30)
        client.connect_async("127.0.0.1", self.config.port, 10)
        client.loop_start()
        self._started = True

    def _on_connect(self, client, _userdata, _flags, reason_code, *_extra) -> None:
        connected = reason_code == 0 or getattr(reason_code, "value", None) == 0
        if not connected:
            self.status_changed.emit(f"MQTT 订阅连接被拒绝：{reason_code}", False)
            return
        for topic in self.config.topics:
            client.subscribe(topic, qos=self.config.qos)
        self.status_changed.emit("MQTT 数据订阅已连接", True)

    def _on_disconnect(self, _client, _userdata, _flags, reason_code, *_extra) -> None:
        self.status_changed.emit(f"MQTT 数据订阅已断开：{reason_code}", False)

    def _on_connect_fail(self, *_args) -> None:
        self.status_changed.emit("MQTT 数据订阅连接失败，正在重试", False)

    def _on_message(self, _client, _userdata, message) -> None:
        self.message_received.emit(str(message.topic), bytes(message.payload))

    def stop(self) -> None:
        if not self._client:
            return
        try:
            self._client.disconnect()
        finally:
            self._client.loop_stop()
            self._client = None
            self._started = False


class MqttMonitoringRuntime(QObject):
    def __init__(self, config: MqttMonitoringConfig, source, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.source = source
        self.broker = MqttBrokerService(config, self)
        self.subscriber = MqttSubscriber(config, parent=self)
        self.broker.status_changed.connect(self._on_broker_status)
        self.subscriber.status_changed.connect(source.set_module_status)
        self.subscriber.message_received.connect(source.process_message)

    def start(self) -> None:
        self.broker.start()

    @Slot(str, bool)
    def _on_broker_status(self, message: str, healthy: bool) -> None:
        self.source.set_module_status(message, healthy)
        if healthy:
            self.subscriber.start()

    def stop(self) -> None:
        self.subscriber.stop()
        self.broker.stop()
