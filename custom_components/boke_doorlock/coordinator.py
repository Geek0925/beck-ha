"""Data update coordinator: REST polling + optional MQTT push + unlock records."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DEFAULT_ALERT_RESET,
    DEFAULT_RECORD_INTERVAL,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    EVENT_NOTIFICATION,
    EVENT_UNLOCK,
)
from .jfcloud import JFCloudClient, JFCloudError, LockDevice, LockStatus
from .jfcloud.messages import ALERT_CATEGORIES, Notification, classify
from .jfcloud.records import OpenRecord, dedupe

_LOGGER = logging.getLogger(__name__)


def record_signal(sn: str) -> str:
    return f"{DOMAIN}_record_{sn}"


def message_signal(sn: str) -> str:
    return f"{DOMAIN}_message_{sn}"


class BokeData:
    """Aggregated cloud state."""

    def __init__(
        self,
        client: JFCloudClient,
        devices: dict[str, LockDevice],
        statuses: dict[str, LockStatus],
        records: dict[str, list[OpenRecord]],
        messages: dict[str, list[Notification]] | None = None,
        alerts: dict[str, dict[str, datetime]] | None = None,
    ) -> None:
        self.client = client
        self.devices = devices
        self.statuses = statuses
        self.records = records
        self.messages = messages or {}
        self.alerts = alerts or {}

    def status(self, sn: str) -> LockStatus | None:
        return self.statuses.get(sn)

    def device(self, sn: str) -> LockDevice | None:
        return self.devices.get(sn)

    def records_for(self, sn: str) -> list[OpenRecord]:
        return self.records.get(sn, [])

    def last_record(self, sn: str) -> OpenRecord | None:
        items = self.records_for(sn)
        return items[0] if items else None

    def messages_for(self, sn: str) -> list[Notification]:
        return self.messages.get(sn, [])

    def last_message(self, sn: str) -> Notification | None:
        items = self.messages_for(sn)
        return items[0] if items else None

    def alert_since(self, sn: str, category: str) -> datetime | None:
        return (self.alerts.get(sn) or {}).get(category)

    def last_unlock_user(self, sn: str) -> str | None:
        """Newest record wins, then the newest unlock notification."""
        record = self.last_record(sn)
        if record and record.user_name:
            return record.user_name
        for message in self.messages_for(sn):
            if message.is_unlock and message.user_name:
                return message.user_name
        status = self.status(sn)
        return status.last_user if status else None


class BokeCoordinator(DataUpdateCoordinator[BokeData]):
    """Polls devices/datapoints and periodically harvests unlock records."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: JFCloudClient,
        sns: list[str] | None = None,
        update_interval: timedelta | None = DEFAULT_UPDATE_INTERVAL,
        record_interval: int = DEFAULT_RECORD_INTERVAL,
        alert_reset: int = DEFAULT_ALERT_RESET,
    ) -> None:
        # update_interval 为 None 时 HA 不会做周期性轮询（避免风控）
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=update_interval)
        self.client = client
        self.sns = sns or []
        self.record_interval = record_interval
        self._records: dict[str, list[OpenRecord]] = {}
        self._last_record_fetch = 0.0
        self._known_record_ids: set[str] = set()
        self._messages: dict[str, list[Notification]] = {}
        self._known_message_ids: set[str] = set()
        self._messages_seeded = False
        self._alerts: dict[str, dict[str, datetime]] = {}
        self._alert_timers: dict[tuple[str, str], Any] = {}
        self.alert_reset = alert_reset
        self.push_listener: Any = None

    # --------------------------------------------------------------- helpers
    async def _async_update_data(self) -> BokeData:
        try:
            items = await self.client.list_devices()
            devices: dict[str, LockDevice] = {}
            for item in items:
                device = LockDevice.from_api(item)
                if not device.sn:
                    continue
                if self.sns and device.sn not in self.sns:
                    continue
                devices[device.sn] = device

            if not devices:
                raise UpdateFailed("no door locks returned by the cloud account")

            statuses: dict[str, LockStatus] = {}
            try:
                rows: list[dict[str, Any]] = await self.client.get_prop_status(list(devices))
            except JFCloudError as err:
                _LOGGER.debug("prop status failed, keeping previous values: %s", err)
                rows = []
            for row in rows:
                sn = str(row.get("sn") or row.get("deviceSn") or "")
                props = row.get("propStatus") or row.get("props") or row.get("data") or {}
                if isinstance(props, list):
                    props = {str(p.get("key") or p.get("propName")): p.get("value") for p in props}
                if sn and sn in devices and isinstance(props, dict):
                    statuses[sn] = LockStatus.from_props(sn, props)

            # keep values pushed over MQTT for locks the poll did not return
            previous_statuses = self.data.statuses if self.data else {}
            for sn, previous in previous_statuses.items():
                statuses.setdefault(sn, previous)

            await self._async_refresh_records(devices)
            await self._async_refresh_notifications(devices)
            return BokeData(
                self.client,
                devices,
                statuses,
                self._records,
                self._messages,
                self._alerts,
            )
        except JFCloudError as err:
            raise UpdateFailed(str(err)) from err

    async def _async_refresh_records(self, devices: dict[str, LockDevice]) -> None:
        now = time.monotonic()
        if self._last_record_fetch and now - self._last_record_fetch < self.record_interval:
            return
        self._last_record_fetch = now

        window_ms = 24 * 60 * 60 * 1000
        end_ms = int(time.time() * 1000)
        for sn in devices:
            try:
                rows = await self.client.get_open_records(sn, end_ms - window_ms, end_ms)
            except JFCloudError as err:
                _LOGGER.debug("record fetch for %s failed: %s", sn, err)
                continue

            previous = {record.record_id for record in self._records.get(sn, [])}
            parsed = dedupe(OpenRecord.from_api(row, sn=sn) for row in rows)
            self._records[sn] = parsed[:200]

            for record in parsed:
                if record.record_id in previous or record.record_id in self._known_record_ids:
                    continue
                self._known_record_ids.add(record.record_id)
                self._async_fire_record(sn, record)

    @callback
    def _async_fire_record(self, sn: str, record: OpenRecord) -> None:
        payload = {
            "sn": sn,
            "record_id": record.record_id,
            "method": record.method,
            "user": record.user_name,
            "user_type": record.user_type,
            "timestamp": record.timestamp.isoformat() if record.timestamp else None,
            "image_url": record.image_url,
        }
        _LOGGER.debug("unlock record: %s", payload)
        self.hass.bus.async_fire(EVENT_UNLOCK, payload)
        async_dispatcher_send(self.hass, record_signal(sn), record)

    async def _async_refresh_notifications(
        self, devices: dict[str, LockDevice]
    ) -> None:
        """Poll the notify log; fire events only for messages seen after startup."""
        try:
            rows = await self.client.get_notify_logs(size=20)
        except JFCloudError as err:
            _LOGGER.debug("notify log fetch failed: %s", err)
            return
        if not rows:
            self._messages_seeded = True
            return

        fresh: list[Notification] = []
        for row in rows:
            message = Notification.from_api(row)
            if message.sn and message.sn not in devices:
                continue
            self._messages.setdefault(message.sn, [])
            bucket = self._messages[message.sn]
            if any(m.message_id == message.message_id for m in bucket):
                continue
            bucket.append(message)
            bucket.sort(
                key=lambda m: m.timestamp or datetime.min.replace(tzinfo=timezone.utc),
                reverse=True,
            )
            del bucket[50:]
            if message.message_id in self._known_message_ids:
                continue
            self._known_message_ids.add(message.message_id)
            fresh.append(message)

        for message in fresh:
            if self._messages_seeded:
                self._async_fire_notification(message)

        if not self._messages_seeded:
            _LOGGER.debug(
                "notify log seeded with %d existing messages", len(fresh)
            )
        self._messages_seeded = True

    @callback
    def _async_fire_notification(self, message: Notification) -> None:
        payload = {
            "sn": message.sn,
            "message_id": message.message_id,
            "msg_type": message.msg_type,
            "method": message.msg_type or "unknown",
            "user": message.user_name,
            "user_type": message.user_type,
            "timestamp": message.timestamp.isoformat() if message.timestamp else None,
            "image_url": message.image_url,
            "is_unlock": message.is_unlock,
        }
        category = classify(message)
        if category:
            payload["category"] = category
            self._mark_alert(message.sn, category, message.timestamp)
        _LOGGER.debug("notification: %s", payload)
        self.hass.bus.async_fire(EVENT_NOTIFICATION, payload)
        async_dispatcher_send(self.hass, message_signal(message.sn), message)

        if message.is_unlock:
            self.hass.bus.async_fire(
                EVENT_UNLOCK,
                {
                    "sn": message.sn,
                    "record_id": message.message_id,
                    "method": message.msg_type or "unknown",
                    "user": message.user_name,
                    "user_type": message.user_type,
                    "timestamp": payload["timestamp"],
                    "image_url": message.image_url,
                },
            )

    @callback
    def _mark_alert(self, sn: str, category: str, when: datetime | None) -> None:
        """Record an alert and schedule its automatic reset."""
        self._alerts.setdefault(sn, {})[category] = when or datetime.now(timezone.utc)
        if self.alert_reset <= 0:
            return

        key = (sn, category)
        timer = self._alert_timers.pop(key, None)
        if timer is not None:
            timer()

        @callback
        def _expire(_now) -> None:
            self._alert_timers.pop(key, None)
            bucket = self._alerts.get(sn) or {}
            bucket.pop(category, None)
            if self.data is not None:
                self.async_set_updated_data(self.data)

        self._alert_timers[key] = async_call_later(
            self.hass, self.alert_reset, _expire
        )

    @callback
    def alert_active(self, sn: str, category: str) -> bool:
        since = (self._alerts.get(sn) or {}).get(category)
        if since is None:
            return False
        if self.alert_reset <= 0:
            return True
        age = (datetime.now(timezone.utc) - since).total_seconds()
        return age < self.alert_reset

    # ------------------------------------------------------------------ push
    @callback
    def async_apply_push(self, sn: str, props: dict[str, Any]) -> None:
        """Merge an MQTT datapoint update into the current snapshot."""
        if not self.data or sn not in self.data.devices:
            _LOGGER.debug("push for unknown sn %r ignored (%s)", sn, props)
            return
        previous = self.data.statuses.get(sn)
        merged = dict(previous.raw) if previous else {}
        merged.update(props)
        self.data.statuses[sn] = LockStatus.from_props(sn, merged)
        self.async_set_updated_data(self.data)

    # ------------------------------------------------------------- commands
    async def async_set_lock(self, sn: str, locked: bool) -> None:
        from .const import ACTION_LOCK, ACTION_UNLOCK, CMD_FIELD_ACTION, CMD_LOCK, CMD_UNLOCK

        name = CMD_LOCK if locked else CMD_UNLOCK
        await self.client.send_command(
            sn,
            name,
            {CMD_FIELD_ACTION: ACTION_LOCK if locked else ACTION_UNLOCK},
        )
        _LOGGER.debug("sent %s to %s", name, sn)
        await self.async_request_refresh()

    async def async_shutdown(self) -> None:
        for cancel in list(self._alert_timers.values()):
            cancel()
        self._alert_timers.clear()
        if self.push_listener is not None:
            self.push_listener.stop()
            self.push_listener = None
        await super().async_shutdown()
