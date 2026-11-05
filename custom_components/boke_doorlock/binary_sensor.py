"""Binary sensors: tamper, forced open, fault, door state, core cover."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import BokeCoordinator
from .jfcloud.models import LockStatus
from .jfcloud.messages import ALERT_CATEGORIES


@dataclass(frozen=True, kw_only=True)
class BokeBinaryDescription(BinarySensorEntityDescription):
    value_fn: Callable[[LockStatus], bool | None] = lambda _status: None


BINARY_SENSORS: tuple[BokeBinaryDescription, ...] = (
    BokeBinaryDescription(
        key="tampered",
        translation_key="tampered",
        device_class=BinarySensorDeviceClass.TAMPER,
        value_fn=lambda s: s.tampered,
    ),
    BokeBinaryDescription(
        key="forced_open",
        translation_key="forced_open",
        device_class=BinarySensorDeviceClass.SAFETY,
        value_fn=lambda s: s.forced_open,
    ),
    BokeBinaryDescription(
        key="malfunction",
        translation_key="malfunction",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda s: s.malfunction,
    ),
    BokeBinaryDescription(
        key="door",
        translation_key="door",
        device_class=BinarySensorDeviceClass.DOOR,
        value_fn=lambda s: s.door_open,
    ),
    BokeBinaryDescription(
        key="core_cover",
        translation_key="core_cover",
        device_class=BinarySensorDeviceClass.OPENING,
        entity_registry_enabled_default=False,
        value_fn=lambda s: s.core_cover_open,
    ),
    BokeBinaryDescription(
        key="door_reminder",
        translation_key="door_reminder",
        device_class=BinarySensorDeviceClass.DOOR,
        entity_registry_enabled_default=False,
        value_fn=lambda s: s.door_reminder or None,
    ),
)


@dataclass(frozen=True, kw_only=True)
class BokeAlertDescription(BinarySensorEntityDescription):
    category: str = ""


#: driven by notifications (notify log), auto-cleared after ``alert_reset``
ALERT_SENSORS: tuple[BokeAlertDescription, ...] = (
    BokeAlertDescription(
        key="alert_tamper",
        translation_key="alert_tamper",
        device_class=BinarySensorDeviceClass.TAMPER,
        category="tamper",
    ),
    BokeAlertDescription(
        key="alert_forced_open",
        translation_key="alert_forced_open",
        device_class=BinarySensorDeviceClass.SAFETY,
        category="forced_open",
    ),
    BokeAlertDescription(
        key="alert_low_battery",
        translation_key="alert_low_battery",
        device_class=BinarySensorDeviceClass.BATTERY,
        category="low_battery",
    ),
    BokeAlertDescription(
        key="alert_offline",
        translation_key="alert_offline",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        category="offline",
    ),
    BokeAlertDescription(
        key="alert_fault",
        translation_key="alert_fault",
        device_class=BinarySensorDeviceClass.PROBLEM,
        category="fault",
    ),
    BokeAlertDescription(
        key="alert_door_open",
        translation_key="alert_door_open",
        device_class=BinarySensorDeviceClass.DOOR,
        entity_registry_enabled_default=False,
        category="door_open",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: BokeCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [
        BokeBinarySensor(coordinator, sn, description)
        for sn in coordinator.data.devices
        for description in BINARY_SENSORS
    ]
    entities.extend(
        BokeAlertSensor(coordinator, sn, description)
        for sn in coordinator.data.devices
        for description in ALERT_SENSORS
    )
    async_add_entities(entities)


class BokeBinarySensor(CoordinatorEntity[BokeCoordinator], BinarySensorEntity):
    """One alarm datapoint as a binary sensor."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: BokeCoordinator, sn: str, description: BokeBinaryDescription
    ) -> None:
        super().__init__(coordinator)
        self._sn = sn
        self.entity_description = description
        self._attr_unique_id = f"{sn}_{description.key}"
        device = coordinator.data.device(sn)
        self._attr_device_info = {
            "identifiers": {(DOMAIN, sn)},
            "name": device.name if device else sn,
        }

    @property
    def is_on(self) -> bool | None:
        status = self.coordinator.data.status(self._sn)
        return None if status is None else self.entity_description.value_fn(status)


class BokeAlertSensor(CoordinatorEntity[BokeCoordinator], BinarySensorEntity):
    """Turns on when a matching notification arrives, then clears itself."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: BokeCoordinator, sn: str, description: BokeAlertDescription
    ) -> None:
        super().__init__(coordinator)
        self._sn = sn
        self.entity_description = description
        self._attr_unique_id = f"{sn}_{description.key}"
        device = coordinator.data.device(sn)
        self._attr_device_info = {
            "identifiers": {(DOMAIN, sn)},
            "name": device.name if device else sn,
        }

    @property
    def is_on(self) -> bool:
        return self.coordinator.alert_active(self._sn, self.entity_description.category)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        since = self.coordinator.data.alert_since(self._sn, self.entity_description.category)
        message = self.coordinator.data.last_message(self._sn)
        return {
            "since": since.isoformat() if since else None,
            "last_message_type": message.msg_type if message else None,
            "last_message_user": message.user_name if message else None,
        }
