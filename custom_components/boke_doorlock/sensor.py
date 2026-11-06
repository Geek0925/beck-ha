"""Sensors — 全部来自实测可读的 REST 字段。

    battery      电池电量（batteryPower）
    signal       Wi-Fi 信号（wifiSignal）
    wifi_name    连接的 Wi-Fi 名称（wifiName）
    last_unlock_user / last_unlock_method / last_unlock_at
                 最近一次开锁（getDeviceAlarmList）
"""
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
        key="signal",
        translation_key="signal",
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=lambda s: s.signal,
    ),
    BokeSensorDescription(
        key="wifi_name",
        translation_key="wifi_name",
        icon="mdi:wifi",
        value_fn=lambda s: s.wifi_name,
    ),
)


@dataclass(frozen=True, kw_only=True)
class BokeRecordDescription(SensorEntityDescription):
    value_fn: Callable[[OpenRecord], Any] = lambda _record: None


#: 由开锁记录（getDeviceAlarmList）驱动的传感器
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
        icon="mdi:clock-check-outline",
        value_fn=lambda record: record.timestamp,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: BokeCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = []
    for sn in coordinator.data.devices:
        entities.extend(BokeSensor(coordinator, sn, description) for description in SENSORS)
        entities.extend(
            BokeRecordSensor(coordinator, sn, description) for description in RECORD_SENSORS
        )
    async_add_entities(entities)


class BokeSensor(CoordinatorEntity[BokeCoordinator], SensorEntity):
    """Sensor backed by the property snapshot."""

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
            "manufacturer": "杰峰科技",
            "model": (device.model if device else "") or "Wi-Fi 门锁",
            "sw_version": device.firmware if device else None,
            "serial_number": sn,
        }

    @property
    def native_value(self) -> Any:
        status = self.coordinator.data.status(self._sn)
        return None if status is None else self._description.value_fn(status)

    @property
    def available(self) -> bool:
        return bool(super().available and self.coordinator.data.device(self._sn))


class BokeRecordSensor(CoordinatorEntity[BokeCoordinator], SensorEntity):
    """Sensor backed by the newest unlock record (getDeviceAlarmList)."""

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
            "manufacturer": "杰峰科技",
            "model": (device.model if device else "") or "Wi-Fi 门锁",
            "serial_number": sn,
        }

    @property
    def native_value(self) -> Any:
        record = self.coordinator.data.last_record(self._sn)
        if record is None:
            return None
        value = self.entity_description.value_fn(record)
        if self.entity_description.key == "last_unlock_at" and value is not None:
            # 云端给的是本地时间字符串，转成 ISO 供 HA 展示
            try:
                return value.isoformat()
            except AttributeError:
                return None
        return value

    @property
    def available(self) -> bool:
        return bool(super().available and self.coordinator.data.device(self._sn))
