"""Services: query the unlock-record history on demand."""
from __future__ import annotations

import time
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN
from .coordinator import BokeCoordinator
from .jfcloud import JFCloudError
from .jfcloud.records import OpenRecord, dedupe

SERVICE_GET_RECORDS = "get_unlock_records"

SCHEMA = vol.Schema(
    {
        vol.Optional("sn"): str,
        vol.Optional("hours", default=24): vol.All(int, vol.Range(min=1, max=720)),
        vol.Optional("limit", default=50): vol.All(int, vol.Range(min=1, max=500)),
    }
)


async def async_setup_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_GET_RECORDS):
        return

    async def _handle(call: ServiceCall) -> ServiceResponse:
        sns = call.data.get("sn")
        hours = call.data["hours"]
        limit = call.data["limit"]
        results: list[dict] = []

        for coordinator in hass.data.get(DOMAIN, {}).values():
            if not isinstance(coordinator, BokeCoordinator) or not coordinator.data:
                continue
            targets = [sns] if sns else list(coordinator.data.devices)
            end_ms = int(time.time() * 1000)
            for sn in targets:
                if sn not in coordinator.data.devices:
                    continue
                try:
                    rows = await coordinator.client.get_open_records(
                        sn, end_ms - hours * 3600 * 1000, end_ms, size=limit
                    )
                except JFCloudError as err:
                    raise HomeAssistantError(f"open records for {sn} failed: {err}") from err
                records = dedupe(OpenRecord.from_api(row, sn=sn) for row in rows)[:limit]
                results.extend(_serialise(record) for record in records)

        results.sort(key=lambda item: item["timestamp"] or "", reverse=True)
        return {"count": len(results), "records": results}

    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_RECORDS,
        _handle,
        schema=SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )


def _serialise(record: OpenRecord) -> dict:
    return {
        "sn": record.sn,
        "record_id": record.record_id,
        "timestamp": record.timestamp.isoformat() if record.timestamp else None,
        "method": record.method,
        "user": record.user_name,
        "user_type": record.user_type,
        "image_url": record.image_url,
        "raw": dict(record.raw),
    }
