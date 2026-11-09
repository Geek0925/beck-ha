"""Notification log entries (``/v3/message/getNotifyLogList``).

The vendor app turns these into the banner you see right after an unlock, so for
Home Assistant this feed is the fastest event source short of the vendor push
channel. Field names were pulled from the app: ``messageId``, ``msgType``,
``alarmType``, ``alarmTime``, ``deviceSn``, ``picUrl`` plus the user fields
shared with the unlock-record model (``nickName`` / ``userName`` /
``userType``).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping

__all__ = [
    "ALERT_CATEGORIES",
    "Notification",
    "UNLOCK_HINTS",
    "CATEGORY_HINTS",
    "classify",
    "looks_like_unlock",
]

_TIME_KEYS = ("alarmTime", "createTime", "time", "msgTime", "eventTime")
_ID_KEYS = ("messageId", "msgId", "id", "logId", "notifyId")
_TYPE_KEYS = ("msgType", "alarmType", "messageType", "eventType", "type")
_USER_KEYS = ("nickName", "userName", "user", "name")
_IMAGE_KEYS = ("picUrl", "imageUrl", "pic", "coverUrl")

#: substrings that mark a notification as an unlock/handle event
UNLOCK_HINTS = (
    "unlock",
    "open",
    "door",
    "handle",
    "password",
    "finger",
    "card",
    "face",
    "key",
    "开锁",
    "开门",
)


def _pick(item: Mapping[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = item.get(key)
        if value not in (None, ""):
            return value
    return None


def _as_time(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)) or str(value).isdigit():
            number = int(value)
            return datetime.fromtimestamp(
                number / 1000 if number > 10**11 else number, tz=timezone.utc
            )
        text = str(value).replace("/", "-").replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (ValueError, OSError):
        return None


@dataclass(slots=True)
class Notification:
    """One row of the notify log."""

    message_id: str
    sn: str = ""
    msg_type: str = ""
    timestamp: datetime | None = None
    user_name: str | None = None
    user_type: str | None = None
    image_url: str | None = None
    raw: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, item: Mapping[str, Any], *, sn: str = "") -> "Notification":
        device_sn = str(item.get("sn") or item.get("deviceSn") or sn or "")
        message_id = _pick(item, _ID_KEYS)
        timestamp = _as_time(_pick(item, _TIME_KEYS))
        msg_type = str(_pick(item, _TYPE_KEYS) or "")
        if message_id in (None, ""):
            seed = f"{device_sn}|{timestamp}|{msg_type}|{item.get('nickName')}"
            message_id = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]
        user_type = item.get("userType")
        return cls(
            message_id=str(message_id),
            sn=device_sn,
            msg_type=msg_type,
            timestamp=timestamp,
            user_name=_pick(item, _USER_KEYS),
            user_type=str(user_type) if user_type is not None else None,
            image_url=_pick(item, _IMAGE_KEYS),
            raw=dict(item),
        )

    @property
    def is_unlock(self) -> bool:
        return looks_like_unlock(self.msg_type) or looks_like_unlock(str(self.raw))


def looks_like_unlock(value: str) -> bool:
    lowered = value.lower()
    return any(hint in lowered for hint in UNLOCK_HINTS)


#: alert buckets the integration exposes as binary sensors
ALERT_CATEGORIES: tuple[str, ...] = (
    "tamper",
    "forced_open",
    "low_battery",
    "offline",
    "fault",
    "door_open",
)

#: keywords per bucket; matched case-insensitively against the whole row
CATEGORY_HINTS: dict[str, tuple[str, ...]] = {
    "tamper": ("tamper", "pry", "dismantle", "撬", "防拆", "拆卸"),
    "forced_open": ("forced", "force", "break", "violen", "强开", "暴力", "破坏"),
    "low_battery": ("lowbattery", "low_battery", "battery", "lowpower", "低电", "电量低", "换电池"),
    "offline": ("offline", "off_line", "disconnect", "掉线", "离线", "断开"),
    "fault": ("fault", "error", "malfunction", "abnormal", "故障", "异常", "堵转"),
    "door_open": ("dooropen", "door_open", "ajar", "未关", "门未关", "虚掩"),
}


def classify(message: "Notification") -> str | None:
    """Bucket a notification into one of :data:`ALERT_CATEGORIES`."""
    haystack = f"{message.msg_type} {message.raw}".lower()
    for category, hints in CATEGORY_HINTS.items():
        if any(hint in haystack for hint in hints):
            return category
    return None
