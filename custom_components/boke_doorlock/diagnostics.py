"""Diagnostics support (credentials are redacted)."""
from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_APP_SECRET, CONF_PASSWORD, DOMAIN
from .coordinator import BokeCoordinator


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    coordinator: BokeCoordinator = hass.data[DOMAIN][entry.entry_id]
    data = coordinator.data
    return {
        "entry": {
            key: ("**REDACTED**" if key in (CONF_APP_SECRET, CONF_PASSWORD, "token") else value)
            for key, value in {**entry.data, **entry.options}.items()
        },
        "devices": {
            sn: {
                "name": device.name,
                "online": device.online,
                "type": device.device_type,
                "firmware": device.firmware,
            }
            for sn, device in data.devices.items()
        },
        "props": {sn: dict(status.raw) for sn, status in data.statuses.items()},
        "records": {
            sn: [record.raw for record in data.records_for(sn)[:5]]
            for sn in data.devices
        },
        "mqtt": _push_info(coordinator),
    }


def _push_info(coordinator: BokeCoordinator) -> dict:
    """Expose whether the optional push channel is alive and what it saw."""
    listener = coordinator.push_listener
    if listener is None:
        return {"enabled": False}
    return {
        "enabled": True,
        "host": listener.host,
        "port": listener.port,
        "tls": listener.tls,
        "topics": listener.topics,
        "client_id": listener.client_id,
        "messages": listener.messages,
        "last_topic": listener.last_topic,
        "last_payload": listener.last_payload,
    }
