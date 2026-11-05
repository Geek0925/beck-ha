"""杰峰云 MQTT 实时通道（2026-11 实测打通）。

连接参数来自微信小程序 app-service.js::

    url      = wxs://jfmq-v2.jftechws.com:11883/mqtt
    clientId = "mini:" + openId
    username = JWT 里的 account（userInfo.userId）
    password = accessToken

实测：明文 `ws://` 握手会被拒，必须 **WSS**（TLS）才能连上；证书链不校验可连通。
主题（同样来自小程序）::

    /device/{sn}
    /device/{sn}/alarmEvent
    /user/{accountId}

本模块只负责连接/订阅/回调，业务映射在 push.py。
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import ssl
from typing import Any, Callable, Iterable

_LOGGER = logging.getLogger(__name__)

DEFAULT_HOST = "jfmq-v2.jftechws.com"
DEFAULT_PORT = 11883
DEFAULT_PATH = "/mqtt"


def account_from_token(token: str) -> str:
    """从 JWT 里取 ``account``（即 MQTT 用户名）。"""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
        return str(claims.get("account") or "")
    except Exception:  # noqa: BLE001
        return ""


def device_topics(sns: Iterable[str]) -> list[str]:
    topics: list[str] = []
    for sn in sns:
        if not sn:
            continue
        topics.append(f"/device/{sn}")
        topics.append(f"/device/{sn}/alarmEvent")
    return topics


class JFMQTTClient:
    """WSS MQTT 客户端：收到事件后回调 ``on_event(topic, payload)``。"""

    def __init__(
        self,
        token: str,
        sns: Iterable[str],
        on_event: Callable[[str, bytes], Any],
        *,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        path: str = DEFAULT_PATH,
        client_id: str | None = None,
    ) -> None:
        self.token = token
        self.account = account_from_token(token)
        self.sns = [s for s in sns if s]
        self.on_event = on_event
        self.host = host
        self.port = port
        self.path = path
        self.client_id = client_id or "mini:boke_doorlock"
        self._client: Any = None
        self._connected = False

    @property
    def topics(self) -> list[str]:
        topics = device_topics(self.sns)
        if self.account:
            topics.append(f"/user/{self.account}")
        return topics

    async def async_start(self) -> bool:
        """连接并订阅；paho 是 HA 自带依赖，缺失时返回 False 而不是抛错。"""
        try:
            import paho.mqtt.client as mqtt
        except ImportError:  # pragma: no cover - 未安装 MQTT 集成的环境
            _LOGGER.warning("paho-mqtt 不可用，跳过 MQTT 实时通道")
            return False
        if not self.account or not self.token:
            _LOGGER.warning("缺少 account/token，跳过 MQTT 实时通道")
            return False

        client = mqtt.Client(
            client_id=self.client_id,
            transport="websockets",
            protocol=mqtt.MQTTv311,
        )
        client.username_pw_set(self.account, self.token)
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        client.tls_set_context(ctx)
        client.tls_insecure_set(True)
        client.ws_set_options(path=self.path, headers={"Sec-WebSocket-Protocol": "mqtt"})

        def _on_connect(_client, _userdata, _flags, rc):
            self._connected = rc == 0
            if rc == 0:
                for topic in self.topics:
                    _client.subscribe(topic)
                _LOGGER.debug("MQTT 已连接并订阅: %s", self.topics)
            else:
                _LOGGER.warning("MQTT 连接被拒, rc=%s", rc)

        def _on_message(_client, _userdata, message):
            try:
                self.on_event(message.topic, bytes(message.payload))
            except Exception:  # noqa: BLE001
                _LOGGER.exception("MQTT 消息处理失败: %s", message.topic)

        client.on_connect = _on_connect
        client.on_message = _on_message
        self._client = client

        def _run() -> None:
            client.connect(self.host, self.port, keepalive=30)
            client.loop_forever(retry_first_connection=True)

        loop = asyncio.get_running_loop()
        loop.run_in_executor(None, _run)
        await asyncio.sleep(2)
        return self._connected

    async def async_stop(self) -> None:
        if not self._client:
            return
        try:
            self._client.disconnect()
            self._client.loop_stop()
        except Exception:  # noqa: BLE001
            pass
        self._client = None
        self._connected = False
