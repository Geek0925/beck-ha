"""博克 (BOKE) Wi-Fi door lock integration for Home Assistant."""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed

from .const import (
    CONF_ALERT_RESET,
    CONF_APP_KEY,
    CONF_APP_SECRET,
    CONF_AREA_CODE,
    CONF_HOST,
    CONF_MOVE_CARD,
    CONF_PASSWORD,
    CONF_PASSWORD_ENCODING,
    CONF_RECORD_INTERVAL,
    CONF_SCAN_INTERVAL,
    CONF_SNS,
    CONF_UUID,
    CONF_USERNAME,
    DEFAULT_ALERT_RESET,
    DEFAULT_APP_KEY,
    DEFAULT_APP_SECRET,
    DEFAULT_AREA_CODE,
    DEFAULT_HOST,
    DEFAULT_MOVE_CARD,
    DEFAULT_MQTT_HOST,
    DEFAULT_MQTT_PORT,
    DEFAULT_MQTT_TOPICS,
    DEFAULT_PASSWORD_ENCODING,
    DEFAULT_RECORD_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .coordinator import BokeCoordinator
from .jfcloud import JFCloudAuthError, JFCloudClient, JFCloudError
from .services import async_setup_services

_LOGGER = logging.getLogger(__name__)

#: 只有云端 REST 能读到的能力才建实体：锁状态/门磁/防拆走 P2P，故不提供 lock 实体
PLATFORMS_LIST: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.EVENT,
]

#: cache key for the session token so a restart does not always re-login
TOKEN_KEY = "token"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up one iCSee Home account."""
    data = {**entry.data, **entry.options}
    client = JFCloudClient(
        uuid=data.get(CONF_UUID) or "xmeye",
        app_key=data.get(CONF_APP_KEY, DEFAULT_APP_KEY),
        app_secret=data.get(CONF_APP_SECRET, DEFAULT_APP_SECRET),
        host=data.get(CONF_HOST) or "cn-jvss.jftechws.com",
        moved_card=int(data.get(CONF_MOVE_CARD, DEFAULT_MOVE_CARD)),
        login_name=data.get(CONF_USERNAME, ""),
        password=data.get(CONF_PASSWORD, ""),
        area_code=data.get(CONF_AREA_CODE, DEFAULT_AREA_CODE),
        password_encoding=data.get(CONF_PASSWORD_ENCODING, DEFAULT_PASSWORD_ENCODING),
    )

    # 每次启动都重新登录：缓存的 token 可能已失效或属于别的区域分片
    try:
        await client.login()
    except JFCloudAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err

    sns = [sn.strip() for sn in (data.get(CONF_SNS) or "").split(",") if sn.strip()]
    # 轮询间隔：0 或未配置 → 事件驱动（不做周期性请求，避免触发云端风控）
    scan_interval = int(data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL) or 0)
    coordinator = BokeCoordinator(
        hass,
        client,
        sns,
        update_interval=(
            timedelta(seconds=scan_interval) if scan_interval > 0 else None
        ),
        record_interval=int(data.get(CONF_RECORD_INTERVAL, DEFAULT_RECORD_INTERVAL)),
        alert_reset=int(data.get(CONF_ALERT_RESET, DEFAULT_ALERT_RESET)),
    )
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS_LIST)
    await async_setup_services(hass)
    await _start_mqtt_safe(hass, entry, coordinator, data)
    await _cleanup_ha_discovery(hass, coordinator)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _cleanup_ha_discovery(hass, coordinator) -> None:
    """删除早期版本注册到 HA MQTT 的 Discovery 实体。

    REST 数据只更新原生实体；MQTT 只用于转发官方推送，因此不再注册
    Discovery（否则会留下永远为空、和原生实体重复的实体）。
    """
    try:
        from .jfcloud.ha_mqtt import discovery_payloads, ha_mqtt_available, async_publish

        if not ha_mqtt_available(hass):
            return
        devices = coordinator.data.devices if coordinator.data else {}
        for sn in devices:
            for topic, _payload in discovery_payloads(sn, sn):
                # 空 payload + retain 会删除该 Discovery 实体
                await async_publish(hass, topic, "")
        _LOGGER.debug("已清理 HA MQTT Discovery 实体")
    except Exception:  # noqa: BLE001
        _LOGGER.debug("清理 HA MQTT Discovery 失败", exc_info=True)


async def _start_mqtt_safe(hass, entry, coordinator, data) -> None:
    """连接杰峰官方 MQTT，把消息桥接到 HA 内置 MQTT。

    小程序参数（实测可连，WSS 必需）：
        wss://jfmq-v2.jftechws.com:11883/mqtt
        username = JWT account, password = accessToken
    消息处理：解析后更新实体，同时转发到 HA 内置 MQTT（mqtt.publish）。
    """
    try:
        from .jfcloud.ha_mqtt import async_publish, ha_mqtt_available
        from .jfcloud.message_map import classify
        from .jfcloud.mqtt import JFMQTTClient

        token = getattr(coordinator.client, "token", None)
        if not token:
            _LOGGER.debug("没有 token，跳过杰峰 MQTT")
            return
        devices = (coordinator.data.devices if coordinator.data else {}) or {}
        sns = list(devices.keys())
        if not sns:
            _LOGGER.debug("没有设备，跳过杰峰 MQTT")
            return

        def _on_event(topic: str, payload: bytes) -> None:
            hass.loop.call_soon_threadsafe(_handle_mqtt, topic, payload)

        def _handle_mqtt(topic: str, payload: bytes) -> None:
            hass.async_create_task(_process_mqtt(topic, payload))

        async def _process_mqtt(topic: str, payload: bytes) -> None:
            # 原样转发到 HA 内置 MQTT，便于用户自行订阅排查
            if ha_mqtt_available(hass):
                await async_publish(hass, f"boke_doorlock/raw{topic}", payload.decode("utf-8", "replace"))
            event = classify(topic, payload)
            if not event:
                return
            sn = event.get("sn")
            if event.get("kind") == "props" and sn:
                coordinator.async_apply_push(sn, event.get("props") or {})
                if ha_mqtt_available(hass):
                    props = event.get("props") or {}
                    if props.get("battery") is not None:
                        await async_publish(hass, f"boke_doorlock/{sn}/battery", str(props["battery"]))
                    if props.get("wifi_name"):
                        await async_publish(hass, f"boke_doorlock/{sn}/wifi", str(props["wifi_name"]))
                    if props.get("signal") is not None:
                        await async_publish(hass, f"boke_doorlock/{sn}/signal", str(props["signal"]))
                    if props.get("lock_state") is not None:
                        await async_publish(hass, f"boke_doorlock/{sn}/online", "ON")
            elif event.get("kind") == "alarm":
                from .const import EVENT_UNLOCK

                hass.bus.async_fire(
                    EVENT_UNLOCK,
                    {
                        "sn": sn,
                        "method": event.get("method"),
                        "user": event.get("user"),
                        "timestamp": event.get("time"),
                        "source": "mqtt",
                    },
                )

        listener = JFMQTTClient(token, sns, _on_event)
        if await listener.async_start():
            _LOGGER.info("杰峰 MQTT 已连接并桥接到 HA: %s", listener.topics)
            async def _stop_listener() -> None:
                await listener.async_stop()

            entry.async_on_unload(_stop_listener)
        else:
            _LOGGER.debug("杰峰 MQTT 未连接（不影响其他功能）")
    except Exception:  # noqa: BLE001
        _LOGGER.debug("杰峰 MQTT 启动失败", exc_info=True)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS_LIST)
    if unloaded:
        coordinator: BokeCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        if coordinator.push_listener is not None:
            coordinator.push_listener.stop()
            coordinator.push_listener = None
        await coordinator.client.close()
    return unloaded


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
