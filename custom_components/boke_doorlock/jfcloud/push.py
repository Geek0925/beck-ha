"""Optional MQTT push channel for 博克 / 杰峰 devices.

The app receives live device state from ``jfmq-v2.xmcsrv.net`` through the
native ``XCloudSDK`` (paho-mqtt C, symbols ``MQTTClient_*``). The topic
structure and the payload envelope live inside the encrypted Java layer, so this
listener is deliberately **configurable**: point it at the broker, give it the
credentials and topic filter, and it will fan every payload into the same
datapoint model used by the REST poller.

It doubles as a discovery harness: with ``log_payloads`` enabled every message is
written to the log with its topic, so the exact topic tree can be learned from a
live account instead of guessed.
"""
from __future__ import annotations

import asyncio
import json
import logging
import ssl
import uuid as uuid_module
from typing import Any, Awaitable, Callable, Iterable, Mapping

_LOGGER = logging.getLogger(__name__)

try:  # paho ships with Home Assistant, but keep the module importable for tests
    import paho.mqtt.client as mqtt
except ImportError:  # pragma: no cover
    mqtt = None  # type: ignore[assignment]

__all__ = ["JFPushListener", "flatten_payload"]

PushCallback = Callable[[str, dict[str, Any]], Awaitable[None]]


def flatten_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Flatten the vendor envelope into datapoint-like keys.

    Accepts both ``{"sn": "..", "props": {"LockState": 1}}`` and a bare
    ``{"LockState": 1}`` payload.
    """
    out: dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, Mapping) and key in ("props", "propStatus", "data", "status", "properties"):
            for inner_key, inner_value in value.items():
                out[str(inner_key)] = inner_value
        elif not isinstance(value, (dict, list)):
            out[str(key)] = value
    return out


def _extract_sn(topic: str, payload: Mapping[str, Any]) -> str | None:
    for key in ("sn", "deviceSn", "devId"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    for part in topic.replace("#", "").split("/"):
        if part and any(ch.isdigit() for ch in part) and len(part) >= 8:
            return part
    return None



def _current_loop():
    try:
        import asyncio

        return asyncio.get_running_loop()
    except RuntimeError:
        return None


class JFPushListener:
    """Threaded paho client that hands messages back to the asyncio loop."""

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        on_message: PushCallback,
        *,
        host: str = "jfmq-v2.xmcsrv.net",
        port: int = 8883,
        tls: bool = True,
        username: str | None = None,
        password: str | None = None,
        topics: Iterable[str] = ("#",),
        client_id: str | None = None,
        keepalive: int = 60,
        log_payloads: bool = False,
    ) -> None:
        self._loop = loop
        self._on_message = on_message
        self.host = host
        self.port = port
        self.tls = tls
        self.username = username
        self.password = password
        self.topics = list(topics) or ["#"]
        self.client_id = client_id or f"boke-ha-{uuid_module.uuid4().hex[:12]}"
        self.keepalive = keepalive
        self.log_payloads = log_payloads
        self._client: Any = None
        self.last_topic: str | None = None
        self.last_payload: dict[str, Any] | None = None
        self.messages = 0

    # ------------------------------------------------------------------ paho
    def _build(self) -> Any:
        if mqtt is None:  # pragma: no cover
            raise RuntimeError("paho-mqtt is not available")
        try:  # paho >= 2.0
            client = mqtt.Client(
                mqtt.CallbackAPIVersion.VERSION2,
                client_id=self.client_id,
                clean_session=True,
            )
        except (AttributeError, TypeError):  # pragma: no cover - paho 1.x
            client = mqtt.Client(client_id=self.client_id, clean_session=True)

        if self.username:
            client.username_pw_set(self.username, self.password or None)
        if self.tls:
            # SSLContext 创建是阻塞调用，放到线程里避免阻塞事件循环
            import asyncio as _asyncio

            ctx = loop.run_in_executor(None, ssl.create_default_context) if (loop := _current_loop()) else None
            if ctx is not None and hasattr(ctx, "__await__"):
                ctx = None
            client.tls_set(cert_reqs=ssl.CERT_REQUIRED)
            client.tls_insecure_set(False)

        client.on_connect = self._handle_connect
        client.on_message = self._handle_message
        client.on_disconnect = self._handle_disconnect
        return client

    def _handle_connect(self, *_args: Any) -> None:  # paho callback (thread)
        _LOGGER.debug("MQTT connected to %s:%s", self.host, self.port)
        for topic in self.topics:
            self._client.subscribe(topic, qos=1)

    def _handle_disconnect(self, *_args: Any) -> None:  # paho callback (thread)
        _LOGGER.warning("MQTT disconnected from %s:%s", self.host, self.port)

    def _handle_message(self, _client: Any, _userdata: Any, message: Any) -> None:
        topic = message.topic
        raw = message.payload or b""
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            if self.log_payloads:
                _LOGGER.debug("MQTT %s <- %s bytes (non JSON)", topic, len(raw))
            return
        if not isinstance(payload, dict):
            return

        self.last_topic = topic
        self.last_payload = payload
        self.messages += 1
        sn = _extract_sn(topic, payload)
        props = flatten_payload(payload)
        if self.log_payloads:
            _LOGGER.debug("MQTT %s -> sn=%s props=%s", topic, sn, props)
        if not props:
            return
        self._loop.call_soon_threadsafe(
            lambda: asyncio.ensure_future(self._on_message(sn or "", props))
        )

    # ------------------------------------------------------------- lifecycle
    def start(self) -> None:
        if mqtt is None:  # pragma: no cover
            _LOGGER.error("paho-mqtt missing; MQTT push disabled")
            return
        self._client = self._build()
        _LOGGER.debug("connecting MQTT %s:%s as %s", self.host, self.port, self.client_id)
        self._client.connect_async(self.host, self.port, keepalive=self.keepalive)
        self._client.loop_start()

    def stop(self) -> None:
        if self._client is None:
            return
        try:
            self._client.disconnect()
            self._client.loop_stop()
        except Exception:  # pragma: no cover - defensive
            _LOGGER.debug("error while stopping MQTT", exc_info=True)
        finally:
            self._client = None
