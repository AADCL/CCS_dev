"""Python 3.6 compatible MQTT transport with retained presence."""

import json
from threading import Lock


def _json(payload):
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


class MqttPublisher(object):
    """Publish fresh health snapshots only while a Paho client is connected."""

    def __init__(self, config, health, logger, client_factory=None):
        self._config = config
        self._health = health
        self._logger = logger
        self._lock = Lock()
        self._connected = False
        if client_factory is None:
            try:
                from paho.mqtt.client import Client
            except ImportError as exc:
                raise RuntimeError("paho-mqtt is required; install python3-paho-mqtt") from exc
            client_factory = Client
        self._client = client_factory(client_id=config.client_id, clean_session=True)
        self._client.reconnect_delay_set(min_delay=1, max_delay=60)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_connect_fail = self._on_connect_fail
        self._client.will_set(
            config.topic("presence"),
            _json(health.presence_payload("offline")),
            qos=config.mqtt.qos,
            retain=True,
        )

    @property
    def connected(self):
        with self._lock:
            return self._connected

    def start(self):
        self._logger.info(
            "mqtt_connect_requested host=%s port=%s client_id=%s",
            self._config.mqtt.ground_station_ip,
            self._config.mqtt.port,
            self._config.client_id,
        )
        self._client.connect_async(
            self._config.mqtt.ground_station_ip,
            self._config.mqtt.port,
            self._config.mqtt.keepalive_seconds,
        )
        self._client.loop_start()

    def _on_connect(self, client, _userdata, _flags, reason_code, *_extra):
        try:
            success = int(reason_code) == 0
        except (TypeError, ValueError):
            success = False
        with self._lock:
            self._connected = success
        if not success:
            self._logger.error("mqtt_connect_rejected reason=%s", reason_code)
            return
        self._logger.info("mqtt_connected")
        self._publish_raw("presence", self._health.presence_payload("online"), retain=True)
        # Stale telemetry is dropped during a disconnect; send the current snapshot on recovery.
        self.publish_heartbeat()
        self.publish_status()

    def _on_connect_fail(self, _client, _userdata):
        self._logger.warning("mqtt_connect_failed retry_backoff_seconds=1_to_60")

    def _on_disconnect(self, _client, _userdata, reason_code, *_extra):
        with self._lock:
            previously_connected = self._connected
            self._connected = False
        if previously_connected:
            self._logger.warning("mqtt_disconnected reason=%s", reason_code)
        else:
            self._logger.warning("mqtt_disconnected_before_connection reason=%s", reason_code)

    def _publish_raw(self, topic_name, payload, retain=False):
        if not self.connected:
            return False
        info = self._client.publish(
            self._config.topic(topic_name),
            _json(payload),
            qos=self._config.mqtt.qos,
            retain=retain,
        )
        result = getattr(info, "rc", 0)
        if result not in (0, None):
            self._logger.warning("mqtt_publish_failed topic=%s result=%s", topic_name, result)
            return False
        message_type = payload.get("message_type", "unknown")
        sequence = payload.get("sequence", "-")
        if topic_name == "heartbeat":
            self._logger.info(
                "heartbeat_sent topic=%s message_type=%s sequence=%s",
                self._config.topic(topic_name),
                message_type,
                sequence,
            )
        else:
            self._logger.info(
                "mqtt_data_sent topic=%s message_type=%s sequence=%s",
                self._config.topic(topic_name),
                message_type,
                sequence,
            )
        return True

    def publish_heartbeat(self):
        return self._publish_raw("heartbeat", self._health.payload("heartbeat"))

    def publish_status(self):
        return self._publish_raw("status", self._health.payload("status"))

    def stop(self):
        if self.connected:
            self._publish_raw("presence", self._health.presence_payload("offline"), retain=True)
        with self._lock:
            self._connected = False
        try:
            self._client.disconnect()
        finally:
            self._client.loop_stop()
        self._logger.info("mqtt_stopped")
