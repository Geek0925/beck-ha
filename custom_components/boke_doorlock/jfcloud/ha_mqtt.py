"""把门锁数据桥接到 Home Assistant 内置 MQTT（无需用户配置 broker）。

HA 里装好官方 MQTT 集成（HAOS 通常自带 Mosquitto）即可：
本模块通过 ``mqtt.publish`` 服务发布 MQTT Discovery 与状态。

只发布云端 REST 真能读到的数据：
    在线状态、电量、Wi-Fi 名称/信号、最近一次开锁（人/方式/时间）
"""
from __future__ import annotations

import json
import logging
from typing import Any

_LOGGER = logging.getLogger(__name__)

DISCOVERY_PREFIX = "homeassistant"
TOPIC_ROOT = "boke_doorlock"


def ha_mqtt_available(hass: Any) -> bool:
    """HA 是否装了 MQTT 集成并注册了 publish 服务。"""
    try:
        return bool(hass.services.has_service("mqtt", "publish"))
    except Exception:  # noqa: BLE001
        return False


def _node(sn: str) -> str:
    return f"boke_{sn}"


def discovery_payloads(sn: str, name: str) -> list[tuple[str, dict[str, Any]]]:
    """返回 (topic, payload)：在线、电量、Wi-Fi、最近开锁人/方式/时间。"""
    node = _node(sn)
    device = {
        "identifiers": [node],
        "name": name or sn,
        "manufacturer": "杰峰科技",
        "model": "Wi-Fi 门锁",
        "serial_number": sn,
    }

    def topic(suffix: str) -> str:
        return f"{TOPIC_ROOT}/{sn}/{suffix}"

    return [
        (
            f"{DISCOVERY_PREFIX}/binary_sensor/{node}/online/config",
            {
                "device": device,
                "name": "在线",
                "unique_id": f"{node}_online",
                "state_topic": topic("online"),
                "payload_on": "ON",
                "payload_off": "OFF",
                "device_class": "connectivity",
            },
        ),
        (
            f"{DISCOVERY_PREFIX}/sensor/{node}/battery/config",
            {
                "device": device,
                "name": "电量",
                "unique_id": f"{node}_battery",
                "state_topic": topic("battery"),
                "unit_of_measurement": "%",
                "device_class": "battery",
                "state_class": "measurement",
            },
        ),
        (
            f"{DISCOVERY_PREFIX}/sensor/{node}/wifi/config",
            {
                "device": device,
                "name": "Wi-Fi 名称",
                "unique_id": f"{node}_wifi",
                "state_topic": topic("wifi"),
                "icon": "mdi:wifi",
            },
        ),
        (
            f"{DISCOVERY_PREFIX}/sensor/{node}/signal/config",
            {
                "device": device,
                "name": "Wi-Fi 信号",
                "unique_id": f"{node}_signal",
                "state_topic": topic("signal"),
                "unit_of_measurement": "dBm",
                "device_class": "signal_strength",
                "state_class": "measurement",
                "entity_registry_enabled_default": True,
            },
        ),
        (
            f"{DISCOVERY_PREFIX}/sensor/{node}/last_user/config",
            {
                "device": device,
                "name": "最近开锁人",
                "unique_id": f"{node}_last_user",
                "state_topic": topic("last_user"),
                "icon": "mdi:account-key",
            },
        ),
        (
            f"{DISCOVERY_PREFIX}/sensor/{node}/last_method/config",
            {
                "device": device,
                "name": "最近开锁方式",
                "unique_id": f"{node}_last_method",
                "state_topic": topic("last_method"),
                "icon": "mdi:key-variant",
            },
        ),
        (
            f"{DISCOVERY_PREFIX}/sensor/{node}/last_time/config",
            {
                "device": device,
                "name": "最近开锁时间",
                "unique_id": f"{node}_last_time",
                "state_topic": topic("last_time"),
                "device_class": "timestamp",
                "icon": "mdi:clock-check-outline",
            },
        ),
    ]


async def async_publish(hass: Any, topic: str, payload: Any, *, retain: bool = True) -> None:
    try:
        await hass.services.async_call(
            "mqtt",
            "publish",
            {
                "topic": topic,
                "payload": payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False),
                "retain": retain,
            },
            blocking=False,
        )
    except Exception:  # noqa: BLE001
        _LOGGER.debug("MQTT publish 失败: %s", topic, exc_info=True)


async def async_publish_discovery(hass: Any, sn: str, name: str) -> None:
    for topic, payload in discovery_payloads(sn, name):
        await async_publish(hass, topic, payload)


async def async_publish_snapshot(hass: Any, sn: str, status: Any, record: Any) -> None:
    """把一台锁的当前状态推到 HA MQTT。"""
    if status is not None:
        if status.online is not None:
            await async_publish(hass, f"{TOPIC_ROOT}/{sn}/online", "ON" if status.online else "OFF")
        if status.battery is not None:
            await async_publish(hass, f"{TOPIC_ROOT}/{sn}/battery", str(status.battery))
        if status.wifi_name:
            await async_publish(hass, f"{TOPIC_ROOT}/{sn}/wifi", str(status.wifi_name))
        if status.signal is not None:
            await async_publish(hass, f"{TOPIC_ROOT}/{sn}/signal", str(status.signal))
    if record is not None:
        if record.user_name:
            await async_publish(hass, f"{TOPIC_ROOT}/{sn}/last_user", str(record.user_name))
        if record.method:
            await async_publish(hass, f"{TOPIC_ROOT}/{sn}/last_method", str(record.method))
        if record.timestamp is not None:
            await async_publish(
                hass,
                f"{TOPIC_ROOT}/{sn}/last_time",
                record.timestamp.isoformat(),
            )
