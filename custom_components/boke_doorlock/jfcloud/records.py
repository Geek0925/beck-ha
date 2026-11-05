"""Unlock records (开门记录) for 博克 door locks.

Field names come from the app's Dart snapshot: ``recordType``, ``userName``,
``userType``, ``nickName``, ``userId``, ``createTime``, ``imageUrl``/``picUrl``.
The cloud returns them under ``/v3/deviceOpnLog/getRecordReport``.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

__all__ = ["OpenRecord", "METHOD_HINTS", "normalise_method", "dedupe"]

#: String forms of unlock methods seen in the app's localisation tables.
#: The wire format is usually numeric; these are used when the cloud already
#: sends a label, and to normalise mixed-case variants.
METHOD_HINTS: dict[str, str] = {
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
