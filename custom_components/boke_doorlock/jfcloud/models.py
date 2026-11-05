"""Datapoint model for 博克 (BOKE) Wi-Fi door locks.

Property / event names below were extracted from the Flutter snapshot of
``com.jf.jlinkiot`` 2.5.1 (``lib/arm64-v8a/libapp.so``). The lock is a
datapoint device: the cloud returns a flat ``{PropName: value}`` map and the
app renders it. Because vendors rename datapoints per firmware, every lookup
here goes through a tolerant alias table instead of hard coding one spelling.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

__all__ = ["LockDevice", "LockStatus", "ALIASES"]

#: canonical name -> known spellings seen in the app
ALIASES: dict[str, tuple[str, ...]] = {
    "locked": ("LockState", "LockStatus", "lockState", "LockedInside"),
    "battery": ("Battery", "BatteryPercent", "ElectricQuantity", "Power"),
    "charging": ("ChargeState", "Charging", "ChargeStatus"),
    "tampered": ("LockTampered", "Tamper", "AlarmTamper"),
    "forced_open": ("LockForciblyOpened", "ForciblyOpened", "AlarmForceOpen"),
    "malfunction": ("LockMalfunction", "Fault", "AlarmFault"),
    "door_open": ("DoorState", "DoorOpen", "DoorSensor"),
    "core_cover_open": ("LockCoreCoverOpen",),
    "door_reminder": ("LockDoorReminder",),
    "auto_lock": ("AutoLock", "AutoLockSwitch"),
    "child_lock": ("ChildLock", "ChildLockSwitch"),
    "unlock_count": ("DoorLockOpenCount", "OpenCount", "UnlockCount"),
    "last_unlock": ("LastOpenTime", "DoorLockOpenTime", "LastUnlockTime"),
    "last_user": ("LastOpenUser", "DoorLockAlarmUser", "LastUnlockUser"),
    "signal": ("Signal", "Rssi", "WifiSignal"),
}

_TRUTHY = {1, "1", "true", "True", "on", "ON", True}
_OPEN_STATES = {1, "1", "open", "Open", "unlock", "Unlocked", "UNLOCKED"}


def _lookup(props: Mapping[str, Any], names: tuple[str, ...]) -> Any:
    for name in names:
        if name in props:
            return props[name]
    # case-insensitive fallback
    lowered = {str(k).lower(): v for k, v in props.items()}
    for name in names:
        if name.lower() in lowered:
            return lowered[name.lower()]
    return None


def _as_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "on", "yes", "open", "locked"}
    return bool(value)


def _as_int(value: Any) -> int | None:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


@dataclass(slots=True)
class LockDevice:
    """A lock as listed by ``/v3/device/getUserDeviceListByPage``."""

    sn: str
    name: str = ""
    device_type: str = ""
    online: bool = False
    firmware: str = ""
    raw: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, item: Mapping[str, Any]) -> "LockDevice":
        sn = str(item.get("sn") or item.get("deviceSn") or item.get("devId") or "")
        online = item.get("online", item.get("status", 0))
        return cls(
            sn=sn,
            name=str(item.get("name") or item.get("deviceName") or sn),
            device_type=str(item.get("deviceType") or item.get("type") or ""),
            online=online in _TRUTHY if not isinstance(online, str) else online.lower() in {"1", "true", "online"},
            firmware=str(item.get("version") or item.get("firmwareVersion") or ""),
            raw=dict(item),
        )


@dataclass(slots=True)
class LockStatus:
    """Decoded datapoint snapshot of one lock."""

    sn: str
    locked: bool | None = None
    battery: int | None = None
    charging: bool | None = None
    tampered: bool = False
    forced_open: bool = False
    malfunction: bool = False
    door_open: bool | None = None
    core_cover_open: bool = False
    door_reminder: bool = False
    auto_lock: bool | None = None
    child_lock: bool | None = None
    unlock_count: int | None = None
    last_unlock: str | None = None
    last_user: str | None = None
    signal: int | None = None
    raw: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_props(cls, sn: str, props: Mapping[str, Any]) -> "LockStatus":
        locked_raw = _lookup(props, ALIASES["locked"])
        if isinstance(locked_raw, str):
            locked = locked_raw.strip().lower() in {"lock", "locked", "1", "true", "close", "closed"}
        elif locked_raw is None:
            locked = None
        else:
            locked = bool(locked_raw)

        door_raw = _lookup(props, ALIASES["door_open"])
        door_open = None
        if door_raw is not None:
            door_open = door_raw in _OPEN_STATES if not isinstance(door_raw, (bool, int)) else bool(door_raw)

        return cls(
            sn=sn,
            locked=locked,
            battery=_as_int(_lookup(props, ALIASES["battery"])),
            charging=_as_bool(_lookup(props, ALIASES["charging"])),
            tampered=bool(_as_bool(_lookup(props, ALIASES["tampered"]))),
            forced_open=bool(_as_bool(_lookup(props, ALIASES["forced_open"]))),
            malfunction=bool(_as_bool(_lookup(props, ALIASES["malfunction"]))),
            door_open=door_open,
            core_cover_open=bool(_as_bool(_lookup(props, ALIASES["core_cover_open"]))),
            door_reminder=bool(_as_bool(_lookup(props, ALIASES["door_reminder"]))),
            auto_lock=_as_bool(_lookup(props, ALIASES["auto_lock"])),
            child_lock=_as_bool(_lookup(props, ALIASES["child_lock"])),
            unlock_count=_as_int(_lookup(props, ALIASES["unlock_count"])),
            last_unlock=_lookup(props, ALIASES["last_unlock"]),
            last_user=_lookup(props, ALIASES["last_user"]),
            signal=_as_int(_lookup(props, ALIASES["signal"])),
            raw=dict(props),
        )
