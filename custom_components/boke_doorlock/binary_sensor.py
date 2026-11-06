"""Binary sensors — 只保留云端真能读到的在线状态。"""
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


@dataclass(frozen=True, kw_only=True)
class BokeBinaryDescription(BinarySensorEntityDescription):
    value_fn: Callable[[LockStatus], bool | None] = lambda _status: None


#: 实测只有「在线」能从 REST 读到（statusGlobal）；门磁/防拆/故障都在 P2P，
#: 拿不到就宁可不建实体，避免长期 unknown 误导。
BINARY_SENSORS: tuple[BokeBinaryDescription, ...] = (
    BokeBinaryDescription(
        key="online",
        translation_key="online",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=lambda s: s.online,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: BokeCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        BokeBinarySensor(coordinator, sn, description)
        for sn in coordinator.data.devices
        for description in BINARY_SENSORS
    )


class BokeBinarySensor(CoordinatorEntity[BokeCoordinator], BinarySensorEntity):
    """Binary sensor backed by the property snapshot."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: BokeCoordinator,
        sn: str,
        description: BokeBinaryDescription,
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
    def is_on(self) -> bool | None:
        status = self.coordinator.data.status(self._sn)
        return None if status is None else self.entity_description.value_fn(status)

    @property
    def available(self) -> bool:
        return bool(super().available and self.coordinator.data.device(self._sn))
