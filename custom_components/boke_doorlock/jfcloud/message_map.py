"""MQTT 报文 → 集成内部的统一事件结构（字段名取自小程序 app-service.js）。

小程序里三类 topic：

    /device/{sn}                属性推送，payload 形如 {"props":[{"propCode":...,"propValue":...}]}
    /device/{sn}/status         同上（状态类）
    /device/{sn}/alarmEvent     报警/开门事件，payload 形如
                                {"SerialNumber":"...","alarmType":..,"alarmReason":..,
                                 "nickName":"...","time":"..."}

本模块只做纯解析，不依赖 Home Assistant，便于单测。
"""
from __future__ import annotations

import json
from typing import Any

#: 小程序 getAlarmReasonName 里的映射（节选，覆盖门锁常见场景）
ALARM_REASONS: dict[int, str] = {
    0: "门铃呼叫",
    1: "强制开门",
    2: "指纹尝试开锁失败",
    3: "密码尝试开锁失败",
    4: "卡片尝试开锁失败",
    5: "钥匙尝试开锁失败",
    6: "遥控器尝试开锁失败",
    8: "胁迫开锁报警",
    9: "撬锁报警",
    10: "门未关好",
    11: "门锁被拆",
    13: "陌生人逗留",
    14: "防拆报警",
    15: "门磁异常",
    16: "低电量",
    17: "布防报警",
}

#: 开锁方式（小程序 propCode / alarmType 里的常见值）
UNLOCK_METHODS: dict[str, str] = {
    "fingerprint": "指纹",
    "password": "密码",
    "card": "卡片",
    "key": "钥匙",
    "remote": "遥控",
    "face": "人脸",
    "app": "App",
    "temporary": "临时密码",
}

#: ``AlarmEvent`` 取值 → 中文描述（云端 alarmEvent 报文里实际使用的字段名）
ALARM_EVENTS: dict[str, str] = {
    "doorlockopen": "开门",
    "doorlockclose": "关门",
    "doorlockalarm": "门锁报警",
    "doorlocktamper": "防拆报警",
    "doorlocklowpower": "低电量",
    "doorbellring": "门铃呼叫",
}

#: ``AlarmMsg.MsgType`` → 内部 slug（与 records.METHOD_HINTS 的取值保持一致，
#: 这样 event 实体才能把它识别成一个可触发的事件类型）
MSG_TYPE_SLUGS: dict[str, str] = {
    "fingerprint": "fingerprint",
    "fingerprintmanager": "fingerprint",
    "fingerprintmanger": "fingerprint",
    "password": "password",
    "passwd": "password",
    "passwdmanager": "password",
    "passwdmanger": "password",
    "card": "card",
    "key": "key",
    "remote": "remote",
    "face": "face",
    "app": "app",
    "temporary": "temporary",
    "temp": "temporary",
}

#: 属性名别名 → 内部字段
PROP_ALIASES: dict[str, str] = {
    "lockstate": "lock_state",
    "lockstatus": "lock_state",
    "doorstate": "door_state",
    "battery": "battery",
    "batterylevel": "battery",
    # 云端 props 推送里电量用的是 batteryPower，原先不在别名表里导致整条消息被丢弃
    "batterypower": "battery",
    "electricquantity": "battery",
    "online": "online",
    "onlinestatus": "online",
    "wifiname": "wifi_name",
    "wifisignal": "wifi_signal",
    "lastuser": "last_user",
}


def _to_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _payload_dict(payload: bytes | str) -> dict[str, Any] | None:
    try:
        text = payload.decode("utf-8") if isinstance(payload, (bytes, bytearray)) else str(payload)
        data = json.loads(text)
    except (UnicodeDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def parse_props(data: dict[str, Any]) -> dict[str, Any]:
    """把 ``props`` 数组摊平成内部字段。"""
    out: dict[str, Any] = {}
    props = data.get("props")
    if not isinstance(props, list):
        return out
    for item in props:
        if not isinstance(item, dict):
            continue
        code = str(item.get("propCode") or "").lower()
        key = PROP_ALIASES.get(code)
        if not key:
            continue
        value = item.get("propValue")
        if key in ("lock_state", "online", "battery") and isinstance(value, (int, float, str)):
            out[key] = value
        else:
            out[key] = value
    return out


def _alarm_msg(data: dict[str, Any]) -> dict[str, Any]:
    """展开 ``AlarmMsg``——云端把它塞成了一个 JSON 字符串。

    真实报文长这样::

        {"AlarmEvent":"DoorLockOpen","AlarmMsg":"{\\"MsgType\\":\\"FingerPrint\\",
         \\"NickName\\":\\"莹莹\\",\\"UserId\\":\\"018\\"}", "AlarmTime":"..."}

    开锁方式和开门人只存在这个内层 JSON 里，不展开就永远取不到。
    """
    raw = data.get("AlarmMsg")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip().startswith("{"):
        parsed = _payload_dict(raw)
        if parsed:
            return parsed
    return {}


def parse_alarm(data: dict[str, Any]) -> dict[str, Any]:
    """解析报警/开门事件。"""
    msg = _alarm_msg(data)
    event_key = str(data.get("AlarmEvent") or "").lower()

    reason_code = _to_int(data.get("alarmReason"))
    if reason_code is None:
        reason_code = _to_int(data.get("alarmType"))

    # 开锁方式：优先内层 AlarmMsg.MsgType，其次老的 openType/unlockType
    method_raw = str(
        msg.get("MsgType")
        or data.get("openType")
        or data.get("unlockType")
        or ""
    )
    method_key = method_raw.lower()
    method_slug = MSG_TYPE_SLUGS.get(method_key, "")

    if reason_code is not None:
        reason = ALARM_REASONS.get(reason_code, ALARM_EVENTS.get(event_key, "未知事件"))
    else:
        reason = ALARM_EVENTS.get(event_key, "未知事件")

    return {
        "sn": data.get("SerialNumber") or data.get("deviceNo") or data.get("sn"),
        "message_id": str(data.get("AlarmID") or "") or None,
        "event": data.get("AlarmEvent"),
        "reason_code": reason_code,
        "reason": reason,
        "method": UNLOCK_METHODS.get(method_key) or (method_raw or None),
        "method_slug": method_slug,
        "user": msg.get("NickName") or data.get("nickName") or data.get("userName"),
        "user_type": msg.get("UserType"),
        "time": data.get("AlarmTime") or data.get("time") or data.get("createTime"),
        "raw": data,
    }


def classify(topic: str, payload: bytes | str) -> dict[str, Any] | None:
    """按 topic 分发，返回 ``{"kind": "props"|"alarm", ...}``。"""
    data = _payload_dict(payload)
    if data is None:
        return None
    if topic.endswith("/alarmEvent"):
        event = parse_alarm(data)
        event["kind"] = "alarm"
        return event
    props = parse_props(data)
    if props:
        return {"kind": "props",
                "sn": data.get("SerialNumber") or data.get("deviceNo") or data.get("sn"),
                "props": props, "raw": data}
    if data.get("SerialNumber") or data.get("deviceNo") or data.get("AlarmEvent"):
        event = parse_alarm(data)
        event["kind"] = "alarm"
        return event
    return None
