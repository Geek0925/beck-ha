"""把门锁状态桥接到 Home Assistant 内置 MQTT（不需要用户配置任何 broker）。

用法：HA 里装好官方 MQTT 集成（大多数 HAOS 都有 Mosquitto 加载项）即可，
本模块通过 ``mqtt.publish`` 服务发布 MQTT Discovery 与状态，不需要额外凭据。
"""
from __future__ import annotations

import json
import logging
from typing import Any

_LOGGER = logging.getLogger(__name__)

DISCOVERY_PREFIX = "homeassistant"


def ha_mqtt_available(hass: Any) -> bool:
    """HA 是否装了 MQTT 集成并注册了 publish 服务。"""
    try:
        return bool(hass.services.has_service("mqtt", "publish"))
    except Exception:  # noqa: BLE001
        return False


def _node(sn: str) -> str:
    return f"boke_{sn}"


def discovery_payloads(sn: str, name: str) -> list[tuple[str, dict[str, Any]]]:
    """返回 (topic, payload) 列表：锁状态、电量、开锁事件。"""
    node = _node(sn)
    state_topic = f"boke_doorlock/{sn}/state"
    battery_topic = f"boke_doorlock/{sn}/battery"
    event_topic = f"boke_doorlock/{sn}/unlock"
    base = {"device": {"identifiers": [node], "name": name or sn, "manufacturer": "BOKE", "model": "Wi-Fi Door Lock"}}
    return [
        (
            f"{DISCOVERY_PREFIX}/lock/{node}/lock/config",
            {
                **base,
                "name": "门锁",
                "unique_id": f"{node}_lock",
                "state_topic": state_topic,
                "command_topic": f"boke_doorlock/{sn}/set",
                "payload_lock": "LOCKED",
                "payload_unlock": "UNLOCKED",
                "state_locked": "LOCKED",
                "state_unlocked": "UNLOCKED",
                "value_template": "{{ value }}",
            },
        ),
        (
            f"{DISCOVERY_PREFIX}/sensor/{node}/battery/config",
            {
                **base,
                "name": "电量",
                "unique_id": f"{node}_battery",
                "state_topic": battery_topic,
                "unit_of_measurement": "%",
                "device_class": "battery",
            },
        ),
        (
            f"{DISCOVERY_PREFIX}/sensor/{node}/unlock/config",
            {
                **base,
                "name": "最近开锁",
                "unique_id": f"{node}_unlock",
                "state_topic": event_topic,
                "icon": "mdi:lock-open",
            },
        ),
    ]


async def async_publish(hass: Any, topic: str, payload: Any, *, retain: bool = True) -> None:
    try:
        await hass.services.async_call(
            "mqtt",
            "publish",
            {"topic": topic, "payload": payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False), "retain": retain},
            blocking=False,
        )
    except Exception:  # noqa: BLE001
        _LOGGER.debug("MQTT publish 失败: %s", topic, exc_info=True)


async def async_publish_discovery(hass: Any, sn: str, name: str) -> None:
    for topic, payload in discovery_payloads(sn, name):
        await async_publish(hass, topic, payload)


async def async_publish_state(hass: Any, sn: str, *, lock_state: Any = None,
                              battery: Any = None, unlock: str | None = None) -> None:
    if lock_state is not None:
        await async_publish(hass, f"boke_doorlock/{sn}/state", str(lock_state).upper())
    if battery is not None:
        await async_publish(hass, f"boke_doorlock/{sn}/battery", str(battery))
    if unlock:
        await async_publish(hass, f"boke_doorlock/{sn}/unlock", unlock)
