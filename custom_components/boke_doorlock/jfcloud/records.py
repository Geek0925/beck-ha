"""Unlock records (开门记录) for 博克 door locks.

Field names come from the app's Dart snapshot: ``recordType``, ``userName``,
``userType``, ``nickName``, ``userId``, ``createTime``, ``imageUrl``/``picUrl``.
The cloud returns them under ``/v3/deviceOpnLog/getRecordReport``.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

__all__ = ["OpenRecord", "METHOD_HINTS", "format_local", "normalise_method", "dedupe"]

#: String forms of unlock methods seen in the app's localisation tables.
#: The wire format is usually numeric; these are used when the cloud already
#: sends a label, and to normalise mixed-case variants.
METHOD_HINTS: dict[str, str] = {
    "fingerprint": "fingerprint",
    "face": "face",
    "passwd": "password",
    "passwdmanger": "password",
    "password": "password",
    "card": "card",
    "key": "key",
    "remote": "remote",
    "temp": "temporary",
    "fingermanger": "fingerprint",
    "facemanger": "face",
    "cardmanger": "card",
    "passwordunlock": "password",
    "passwordtryunlock": "password_failed",
    "adminpasswordunlock": "password_admin",
    "fingerprintunlock": "fingerprint",
    "adminfingerprintunlock": "fingerprint_admin",
    "faceunlock": "face",
    "adminfaceunlock": "face_admin",
    "cardunlock": "card",
    "admincardunlock": "card_admin",
    "keyunlock": "key",
    "adminkeyunlock": "key_admin",
    "remoteunlock": "remote",
    "adminremoteunlock": "remote_admin",
    "tempunlock": "temporary",
    "admintempunlock": "temporary_admin",
    "combinationunlock": "combination",
    "emergencyunlock": "emergency",
    "indrbuttonunlock": "inside_button",
    "indrhandleunlock": "inside_handle",
    "fingerveinunlock": "finger_vein",
    "palmveinunlock": "palm_vein",
    "palmprintunlock": "palmprint",
    "irisunlock": "iris",
}

_TIME_KEYS = ("createTime", "recordTime", "openTime", "unlockTime", "time", "eventTime")
_NAME_KEYS = ("nickName", "userName", "name")
_IMAGE_KEYS = ("imageUrl", "picUrl", "pic", "url")
_TYPE_KEYS = ("recordType", "unlockType", "openType", "openWay", "unlockWay", "method")
_ID_KEYS = ("recordId", "id", "dataId", "logId")


def normalise_method(value: Any) -> str:
    """Map a label or code to a stable slug."""
    if value is None:
        return "unknown"
    if isinstance(value, str):
        key = value.replace("_", "").replace(" ", "").lower()
        return METHOD_HINTS.get(key, value.strip() or "unknown")
    return f"type_{value}"


def _first(item: Mapping[str, Any], keys: Iterable[str]) -> Any:
    for key in keys:
        if key in item and item[key] not in (None, ""):
            return item[key]
    return None


def _as_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
        number = int(value)
        if number > 10**11:  # milliseconds
            return datetime.fromtimestamp(number / 1000, tz=timezone.utc)
        return datetime.fromtimestamp(number, tz=timezone.utc)
    if isinstance(value, str):
        text = value.replace("/", "-").replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def _device_tz(tz_name: str | None) -> timezone | Any:
    """设备时区；拿不到就退回 UTC+8（云端时间戳都是北京时间）。"""
    if tz_name:
        try:
            from zoneinfo import ZoneInfo

            return ZoneInfo(tz_name)
        except Exception:  # noqa: BLE001
            pass
    return timezone(timedelta(hours=8))


def _as_local_datetime(value: Any, tz_name: str | None = None) -> datetime | None:
    """云端返回 "YYYY-MM-DD HH:MM:SS"（设备本地时间，无时区标记）。

    必须按**设备时区**解析，否则 HA 容器跑在 UTC 时会差 8 小时。
    """
    if isinstance(value, str):
        text = value.strip()
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(text, fmt)
            except ValueError:
                continue
            return parsed.replace(tzinfo=_device_tz(tz_name)).astimezone(timezone.utc)
    return _as_datetime(value)


def format_local(value: datetime | None, tz_name: str = "Asia/Shanghai") -> str | None:
    """把 UTC 时间格式化成设备本地时间 ``yyyy-MM-dd HH:mm:ss``。"""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(_device_tz(tz_name)).strftime("%Y-%m-%d %H:%M:%S")


@dataclass(slots=True)
class OpenRecord:
    """One unlock event as reported by the cloud."""

    record_id: str
    sn: str = ""
    timestamp: datetime | None = None
    method: str = "unknown"
    user_name: str | None = None
    user_type: str | None = None
    image_url: str | None = None
    raw: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, item: Mapping[str, Any], *, sn: str = "") -> "OpenRecord":
        identifier = _first(item, _ID_KEYS)
        timestamp = _as_datetime(_first(item, _TIME_KEYS))
        if identifier in (None, ""):
            seed = f"{sn}|{timestamp}|{item.get('userName')}|{item.get('recordType')}"
            identifier = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]
        return cls(
            record_id=str(identifier),
            sn=str(item.get("sn") or sn),
            timestamp=timestamp,
            method=normalise_method(_first(item, _TYPE_KEYS)),
            user_name=_first(item, _NAME_KEYS),
            user_type=str(item.get("userType")) if item.get("userType") is not None else None,
            image_url=_first(item, _IMAGE_KEYS),
            raw=dict(item),
        )

    @property
    def is_success(self) -> bool:
        return "failed" not in self.method

    @classmethod
    def from_alarm(
        cls, item: Mapping[str, Any], *, sn: str = "", tz_name: str | None = None
    ) -> "OpenRecord":
        """解析 ``getDeviceAlarmList`` 返回的 AlarmArray 条目。

        结构（实测）::

            {"AlarmId": "261105233855373", "AlarmTime": "2026-11-06 07:38:54",
             "AlarmEvent": "DoorLockOpen",
             "AlarmMsg": "{\"MsgType\":\"FingerPrint\",\"NickName\":\"甜甜妈妈\",
                          \"UserId\":\"018\",\"UserType\":\"FingerManger\"}"}
        """
        message: Mapping[str, Any] = {}
        raw_msg = item.get("AlarmMsg")
        if isinstance(raw_msg, str) and raw_msg.strip().startswith("{"):
            try:
                parsed = json.loads(raw_msg)
                if isinstance(parsed, dict):
                    message = parsed
            except ValueError:
                message = {}
        event = str(item.get("AlarmEvent") or "")
        msg_type = message.get("MsgType") or message.get("msgType") or ""
        method = normalise_method(msg_type or event)
        identifier = item.get("AlarmId") or item.get("id")
        timestamp = _as_local_datetime(item.get("AlarmTime") or item.get("alarmTime"), tz_name)
        if identifier in (None, ""):
            seed = f"{sn}|{event}|{timestamp}|{message.get('NickName')}"
            identifier = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]
        return cls(
            record_id=str(identifier),
            sn=str(item.get("SerialNumber") or sn),
            timestamp=timestamp,
            method=method,
            user_name=message.get("NickName") or message.get("UserName"),
            user_type=message.get("UserType"),
            image_url=item.get("PicUrl") or item.get("picUrl"),
            raw={**dict(item), "event": event, "message": message},
        )

    @property
    def is_unlock(self) -> bool:
        """Whether this alarm record represents a door-open event."""
        event = str(self.raw.get("event") or "").lower()
        return "doorlockopen" in event or "unlock" in event or self.method not in ("unknown", "")


def dedupe(records: Iterable[OpenRecord]) -> list[OpenRecord]:
    """Drop repeats, newest first."""
    seen: set[str] = set()
    out: list[OpenRecord] = []
    for record in sorted(
        records, key=lambda r: r.timestamp or datetime.min.replace(tzinfo=timezone.utc), reverse=True
    ):
        if record.record_id in seen:
            continue
        seen.add(record.record_id)
        out.append(record)
    return out
