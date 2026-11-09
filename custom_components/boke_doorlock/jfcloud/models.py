"""Data models for 博克 door locks — 只保留云端 REST 真正能读到的字段。

实测可读（2026-11 抓包 + 接口验证）::

    getDevicePropStatus   -> batteryPower, wifiSignal, wifiName
    statusGlobal          -> status(online/offline), wanIp, wakeUpStatus
    getUserDeviceListByPage -> deviceName, pid, devMac, description, bindTime ...

实测读不到（走 P2P，不做实体）::

    锁开合状态、门磁、防拆、强制开门、故障、电量充电状态、临时密码数量等
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

__all__ = ["LockDevice", "LockStatus"]

_OPEN_STATES = {"open", "opened", "1", "true"}


def _as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in _OPEN_STATES
    return False


@dataclass(slots=True)
class LockDevice:
    """One door lock as returned by ``getUserDeviceListByPage``."""

    sn: str
    name: str = ""
    device_type: str = ""
    model: str = ""
    firmware: str = ""
    online: bool = False
    mac: str = ""
    room_name: str = ""
    raw: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, item: Mapping[str, Any]) -> "LockDevice":
        sn = str(item.get("sn") or item.get("deviceNo") or item.get("deviceSn") or "")
        online = item.get("online", item.get("status", 0))
        return cls(
            sn=sn,
            name=str(item.get("name") or item.get("deviceName") or sn),
            device_type=str(item.get("deviceType") or item.get("type") or ""),
            model=str(item.get("pid") or item.get("model") or ""),
            firmware=str(item.get("version") or item.get("firmwareVersion") or ""),
            online=online in (1, "1", True, "true")
            if not isinstance(online, str)
            else online.strip().lower() in _OPEN_STATES,
            mac=str(item.get("devMac") or ""),
            raw=dict(item),
        )


@dataclass(slots=True)
class LockStatus:
    """云端能提供的全部状态（就这三个 + 在线状态）。"""

    sn: str
    battery: int | None = None
    signal: int | None = None
    wifi_name: str | None = None
    online: bool | None = None
    wan_ip: str | None = None
    room_name: str | None = None
    raw: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_props(cls, sn: str, props: Mapping[str, Any]) -> "LockStatus":
        return cls(
            sn=sn,
            battery=_as_int(props.get("batteryPower") or props.get("battery")),
            signal=_as_int(props.get("wifiSignal") or props.get("signal")),
            wifi_name=props.get("wifiName"),
            online=None,
            raw=dict(props),
        )

    def with_online(self, *, online: bool | None, wan_ip: str | None) -> "LockStatus":
        """合并 statusGlobal 的在线状态。"""
        return LockStatus(
            sn=self.sn,
            battery=self.battery,
            signal=self.signal,
            wifi_name=self.wifi_name,
            online=online if online is not None else self.online,
            wan_ip=wan_ip or self.wan_ip,
            raw=self.raw,
        )

    def with_room(self, room_name: str | None) -> "LockStatus":
        """合并设备所在房间名（来自 getRoomListByPage）。"""
        return LockStatus(
            sn=self.sn, battery=self.battery, signal=self.signal,
            wifi_name=self.wifi_name, online=self.online, wan_ip=self.wan_ip,
            room_name=room_name or self.room_name, raw=self.raw,
        )

    def with_props(self, props: Mapping[str, Any]) -> "LockStatus":
        """合并 MQTT 推送的属性（同样只有这三个字段可用）。"""
        merged = {**dict(self.raw), **dict(props)}
        fresh = LockStatus.from_props(self.sn, merged)
        fresh.online = self.online
        fresh.wan_ip = self.wan_ip
        return fresh
