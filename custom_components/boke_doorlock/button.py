"""Button: 手动触发一次状态 + 开锁记录刷新。"""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import BokeCoordinator

REFRESH_BUTTON = ButtonEntityDescription(
    key="refresh",
    translation_key="refresh",
    icon="mdi:refresh",
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: BokeCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        BokeRefreshButton(coordinator, sn) for sn in coordinator.data.devices
    )


class BokeRefreshButton(CoordinatorEntity[BokeCoordinator], ButtonEntity):
    """按一下就从云端重新拉取状态与开锁记录。"""

    _attr_has_entity_name = True
    entity_description = REFRESH_BUTTON

    def __init__(self, coordinator: BokeCoordinator, sn: str) -> None:
        super().__init__(coordinator)
        self._sn = sn
        self._attr_unique_id = f"{sn}_refresh"
        device = coordinator.data.device(sn)
        self._attr_device_info = {
            "identifiers": {(DOMAIN, sn)},
            "name": device.name if device else sn,
            "manufacturer": "杰峰科技",
            "model": (device.model if device else "") or "Wi-Fi 门锁",
            "serial_number": sn,
        }

    async def async_press(self) -> None:
        # 强制立刻拉一次状态与记录，不受间隔限制
        self.coordinator.reset_record_timer()
        await self.coordinator.async_refresh()
