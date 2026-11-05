"""Event platform: one ``event`` entity per lock emitting unlock records."""
from __future__ import annotations

from typing import Any

from homeassistant.components.event import EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import BokeCoordinator, message_signal, record_signal
from .jfcloud.messages import Notification
from .jfcloud.records import METHOD_HINTS, OpenRecord

#: every slug the parser can emit, so automations can pick a trigger
EVENT_TYPES: list[str] = sorted(set(METHOD_HINTS.values())) + ["message", "unknown"]


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: BokeCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(BokeUnlockEvent(coordinator, sn) for sn in coordinator.data.devices)


class BokeUnlockEvent(CoordinatorEntity[BokeCoordinator], EventEntity):
    """Fires for each new 开门记录 so automations can react to who/how."""

    _attr_has_entity_name = True
    _attr_translation_key = "unlock"
    _attr_icon = "mdi:door-open"
    _attr_event_types = EVENT_TYPES

    def __init__(self, coordinator: BokeCoordinator, sn: str) -> None:
        super().__init__(coordinator)
        self._sn = sn
        self._attr_unique_id = f"{sn}_unlock_event"
        device = coordinator.data.device(sn)
        self._attr_device_info = {
            "identifiers": {(DOMAIN, sn)},
            "name": device.name if device else sn,
        }

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(self.hass, record_signal(self._sn), self._handle_record)
        )
        self.async_on_remove(
            async_dispatcher_connect(self.hass, message_signal(self._sn), self._handle_message)
        )

    @callback
    def _handle_record(self, record: OpenRecord) -> None:
        self._trigger_event(record.method, self._attributes_for(record))
        self.async_write_ha_state()

    @callback
    def _handle_message(self, message: Notification) -> None:
        event_type = message.msg_type or "message"
        if event_type not in EVENT_TYPES:
            event_type = "message"
        self._trigger_event(
            event_type,
            {
                "message_id": message.message_id,
                "sn": message.sn,
                "msg_type": message.msg_type,
                "user": message.user_name,
                "user_type": message.user_type,
                "timestamp": message.timestamp.isoformat() if message.timestamp else None,
                "image_url": message.image_url,
                "is_unlock": message.is_unlock,
            },
        )
        self.async_write_ha_state()

    @staticmethod
    def _attributes_for(record: OpenRecord) -> dict[str, Any]:
        return {
            "record_id": record.record_id,
            "sn": record.sn,
            "method": record.method,
            "user": record.user_name,
            "user_type": record.user_type,
            "timestamp": record.timestamp.isoformat() if record.timestamp else None,
            "image_url": record.image_url,
            "success": record.is_success,
        }
