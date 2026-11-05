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
    CONF_MQTT_ENABLED,
    CONF_MQTT_HOST,
    CONF_MQTT_LOG_PAYLOADS,
    CONF_MQTT_PASSWORD,
    CONF_MQTT_PORT,
    CONF_MQTT_TLS,
    CONF_MQTT_TOPICS,
    CONF_MQTT_USERNAME,
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
from .jfcloud.push import JFPushListener
from .services import async_setup_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS_LIST: list[Platform] = [
    Platform.LOCK,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
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
    await _start_push(hass, coordinator, data)
    await _start_mqtt_safe(hass, entry, coordinator, data)
    await _publish_ha_discovery(hass, coordinator)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _publish_ha_discovery(hass, coordinator) -> None:
    """把门锁注册到 HA 内置 MQTT（用户无需配置任何 broker 凭据）。"""
    try:
        from .jfcloud.ha_mqtt import async_publish_discovery, ha_mqtt_available

        if not ha_mqtt_available(hass):
            return
        devices = getattr(coordinator, "devices", {}) or {}
        for sn, dev in (devices.items() if hasattr(devices, "items") else []):
            name = getattr(dev, "name", None) or sn
            await async_publish_discovery(hass, sn, name)
        _LOGGER.info("已向 HA 内置 MQTT 发布门锁 Discovery")
    except Exception:  # noqa: BLE001
        _LOGGER.debug("HA MQTT Discovery 发布失败", exc_info=True)


async def _start_mqtt_safe(hass, entry, coordinator, data) -> None:
    """启动 WSS MQTT 实时通道；任何异常都不影响集成加载。"""
    try:
        from .jfcloud.message_map import classify
        from .jfcloud.mqtt import JFMQTTClient
        from .const import EVENT_NOTIFICATION

        token = getattr(coordinator.client, "token", None)
        devices = getattr(coordinator, "devices", {}) or {}
        sns = list(devices.keys()) if hasattr(devices, "keys") else []
        if not token or not sns:
            return

        def _handle(event) -> None:
            try:
                if event.get("kind") == "alarm":
                    hass.bus.async_fire(EVENT_NOTIFICATION, {
                        "sn": event.get("sn"), "reason": event.get("reason"),
                        "user": event.get("user"), "time": event.get("time"),
                        "source": "mqtt",
                    })
                else:
                    coordinator.async_set_updated_data(
                        {"props": event.get("props", {}), "sn": event.get("sn")}
                    )
            except Exception:  # noqa: BLE001
                pass

        def _on_event(topic: str, payload: bytes) -> None:
            event = classify(topic, payload)
            if event:
                hass.loop.call_soon_threadsafe(_handle, event)

        listener = JFMQTTClient(token, sns, _on_event)
        if await listener.async_start():
            entry.async_on_unload(lambda: hass.async_create_task(listener.async_stop()))
    except Exception:  # noqa: BLE001
        _LOGGER.debug("MQTT 实时通道启动失败，忽略", exc_info=True)


async def _start_push(
    hass: HomeAssistant, coordinator: BokeCoordinator, data: dict
) -> None:
    """Start the optional MQTT push listener.

    Topic and payload envelope live inside the vendor's hardened layer, so this
    is a configurable subscriber that doubles as a discovery harness. When no
    credentials are given it asks the cloud for device tokens and uses the
    uuid / token pair, which is how the native SDK authenticates.
    """
    if not data.get(CONF_MQTT_ENABLED):
        return

    topics = [
        topic.strip()
        for topic in str(data.get(CONF_MQTT_TOPICS) or DEFAULT_MQTT_TOPICS).split(",")
        if topic.strip()
    ]
    username = data.get(CONF_MQTT_USERNAME) or coordinator.client.uuid
    password = data.get(CONF_MQTT_PASSWORD) or ""
    source = "config"
    if not password:
        try:
            tokens = await coordinator.client.get_device_token(
                list(coordinator.data.devices)
            )
            password = next(iter(tokens.values()), "")
            source = "device token"
        except JFCloudError as err:
            _LOGGER.debug("could not fetch a device token for MQTT: %s", err)

    listener = JFPushListener(
        hass.loop,
        coordinator.async_apply_push,
        host=data.get(CONF_MQTT_HOST) or DEFAULT_MQTT_HOST,
        port=int(data.get(CONF_MQTT_PORT) or DEFAULT_MQTT_PORT),
        tls=bool(data.get(CONF_MQTT_TLS, True)),
        username=username or None,
        password=password or None,
        topics=topics or ["#"],
        log_payloads=bool(data.get(CONF_MQTT_LOG_PAYLOADS, True)),
    )
    try:
        listener.start()
    except Exception:  # pragma: no cover - broker/TLS dependent
        _LOGGER.exception("could not start the MQTT push listener; falling back to polling")
        return
    coordinator.push_listener = listener
    _LOGGER.info(
        "MQTT push enabled: %s:%s as %s (%s credentials), topics=%s",
        listener.host,
        listener.port,
        listener.client_id,
        source,
        topics,
    )


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
