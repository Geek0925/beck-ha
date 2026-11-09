"""Lock platform: one ``lock`` entity per door lock."""
from __future__ import annotations

from typing import Any

from homeassistant.components.lock import LockEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTR_RAW_PROPS, DOMAIN, MANUFACTURER
from .coordinator import BokeCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: BokeCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        BokeLock(coordinator, sn) for sn in coordinator.data.devices
    )


class BokeLock(CoordinatorEntity[BokeCoordinator], LockEntity):
    """A Wi-Fi door lock controlled through the vendor cloud."""

    _attr_has_entity_name = True
    _attr_name = None

    def __init__(self, coordinator: BokeCoordinator, sn: str) -> None:
        super().__init__(coordinator)
        self._sn = sn
        self._attr_unique_id = f"{sn}_lock"
        device = coordinator.data.device(sn)
        self._attr_device_info = {
            "identifiers": {(DOMAIN, sn)},
            "name": device.name if device else sn,
            "manufacturer": MANUFACTURER,
            "model": (device.device_type if device else "") or "Wi-Fi 门锁",
            "sw_version": device.firmware if device else None,
            "serial_number": sn,
        }

    @property
    def _status(self):
        return self.coordinator.data.status(self._sn)

    @property
    def available(self) -> bool:
        device = self.coordinator.data.device(self._sn)
        return bool(super().available and device and device.online)

    @property
    def is_locked(self) -> bool | None:
        status = self._status
        return None if status is None else status.locked

    @property
    def is_locking(self) -> bool:
        return False

    @property
    def is_unlocking(self) -> bool:
        return False

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        status = self._status
        if status is None:
            return {}
        record = self.coordinator.data.last_record(self._sn)
        notification = self.coordinator.data.last_message(self._sn)
        attrs: dict[str, Any] = {
            "battery_level": status.battery,
            "door_open": status.door_open,
            "auto_lock": status.auto_lock,
            "child_lock": status.child_lock,
            "unlock_count": status.unlock_count,
            "last_unlock_method": record.method if record else None,
            "last_unlock_user": self.coordinator.data.last_unlock_user(self._sn),
            "last_unlock_at": record.timestamp.isoformat()
            if record and record.timestamp
            else status.last_unlock,
            "last_notification": notification.msg_type if notification else None,
            ATTR_RAW_PROPS: status.raw,
        }
        return {k: v for k, v in attrs.items() if v is not None}

    async def async_lock(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_lock(self._sn, True)

    async def async_unlock(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_lock(self._sn, False)
