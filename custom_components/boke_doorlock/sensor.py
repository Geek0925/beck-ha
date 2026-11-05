"""Sensors: battery, unlock counter, last unlock, Wi-Fi signal."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, SIGNAL_STRENGTH_DECIBELS_MILLIWATT
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import BokeCoordinator
from .jfcloud.models import LockStatus
from .jfcloud.records import OpenRecord


@dataclass(frozen=True, kw_only=True)
class BokeSensorDescription(SensorEntityDescription):
    value_fn: Callable[[LockStatus], Any] = lambda _status: None


SENSORS: tuple[BokeSensorDescription, ...] = (
    BokeSensorDescription(
        key="battery",
        translation_key="battery",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: s.battery,
    ),
    BokeSensorDescription(
        key="unlock_count",
        translation_key="unlock_count",
        icon="mdi:counter",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda s: s.unlock_count,
    ),
    BokeSensorDescription(
        key="last_unlock",
        translation_key="last_unlock",
        icon="mdi:lock-clock",
        value_fn=lambda s: s.last_unlock,
    ),
    BokeSensorDescription(
        key="signal",
        translation_key="signal",
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=lambda s: s.signal,
    ),
)


@dataclass(frozen=True, kw_only=True)
class BokeRecordDescription(SensorEntityDescription):
    value_fn: Callable[[OpenRecord], Any] = lambda _record: None


#: sensors backed by the unlock-record feed instead of the datapoint snapshot
RECORD_SENSORS: tuple[BokeRecordDescription, ...] = (
    BokeRecordDescription(
        key="last_unlock_user",
        translation_key="last_unlock_user",
        icon="mdi:account-key",
        value_fn=lambda record: record.user_name,
    ),
    BokeRecordDescription(
        key="last_unlock_method",
        translation_key="last_unlock_method",
        icon="mdi:key-variant",
        value_fn=lambda record: record.method,
    ),
    BokeRecordDescription(
        key="last_unlock_at",
        translation_key="last_unlock_at",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:clock-check-outline",
        value_fn=lambda record: record.timestamp,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: BokeCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[BokeSensor] = []
    for sn in coordinator.data.devices:
        entities.extend(BokeSensor(coordinator, sn, description) for description in SENSORS)
    entities.extend(
        BokeRecordSensor(coordinator, sn, description) for description in RECORD_SENSORS
    )
    async_add_entities(entities)


class BokeSensor(CoordinatorEntity[BokeCoordinator], SensorEntity):
    """One datapoint exposed as a sensor."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: BokeCoordinator, sn: str, description: BokeSensorDescription
    ) -> None:
        super().__init__(coordinator)
        self._sn = sn
        self._description = description
        self.entity_description = description
        self._attr_unique_id = f"{sn}_{description.key}"
        device = coordinator.data.device(sn)
        self._attr_device_info = {
            "identifiers": {(DOMAIN, sn)},
            "name": device.name if device else sn,
        }

    @property
    def native_value(self) -> Any:
        status = self.coordinator.data.status(self._sn)
        return None if status is None else self._description.value_fn(status)


class BokeRecordSensor(CoordinatorEntity[BokeCoordinator], SensorEntity):
    """Sensor backed by the newest unlock record."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: BokeCoordinator, sn: str, description: BokeRecordDescription
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
    def native_value(self) -> Any:
        record = self.coordinator.data.last_record(self._sn)
        if record is not None:
            return self.entity_description.value_fn(record)
        # 回退：用 notify-log 里最新一条（含历史消息）
        note = getattr(self.coordinator, "last_notification", None) or {}
        if not note:
            return None
        key = self.entity_description.key
        if key.endswith("user"):
            return note.get("user") or note.get("nickName")
        if key.endswith("method"):
            return note.get("method") or note.get("logType")
        return note.get("time") or note.get("timestamp")
