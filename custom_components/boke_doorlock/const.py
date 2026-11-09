"""Constants for the 博克 (BOKE) Wi-Fi door lock integration."""
from __future__ import annotations

from datetime import timedelta

from .jfcloud.defaults import APP_KEY as DEFAULT_APP_KEY
from .jfcloud.defaults import APP_UUID as DEFAULT_UUID
from .jfcloud.defaults import MOVE_CARD as DEFAULT_MOVE_CARD
from .jfcloud.defaults import APP_SECRET as DEFAULT_APP_SECRET

DOMAIN = "boke_doorlock"
NAME = "博克门锁 (BOKE Door Lock)"
MANUFACTURER = "BOKE / 杰峰 JFTech"

# --- account (primary auth mode) ------------------------------------------
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_AREA_CODE = "area_code"
CONF_PASSWORD_ENCODING = "password_encoding"

# --- open-platform / signing ----------------------------------------------
CONF_UUID = "uuid"
CONF_APP_KEY = "app_key"
CONF_APP_SECRET = "app_secret"
CONF_HOST = "host"
CONF_MOVE_CARD = "move_card"

CONF_SNS = "sns"
CONF_SCAN_INTERVAL = "scan_interval"

DEFAULT_HOST = "cn-jvss.jftechws.com"
DEFAULT_MQTT_HOST = "jfmq-v2.xmcsrv.net"
DEFAULT_MQTT_PORT = 8883
DEFAULT_MQTT_TOPICS = "#"
DEFAULT_AREA_CODE = "86"
DEFAULT_PASSWORD_ENCODING = "md5"
DEFAULT_MOVE_CARD = 4
# 默认 0 = 不轮询（事件驱动：启动拉一次 + 操作后短窗刷新 + 手动刷新）
DEFAULT_SCAN_INTERVAL = 0
DEFAULT_UPDATE_INTERVAL = timedelta(seconds=DEFAULT_SCAN_INTERVAL)
DEFAULT_RECORD_INTERVAL = 300  # seconds between unlock-record harvests

#: App-level signing credentials, see jfcloud/defaults.py (overridable).

#: Device-side command ids. ``OPLOCK`` is the vendor's access-control command
#: family; override through options if a firmware uses another name.
CMD_LOCK = "OPLOCK"
CMD_UNLOCK = "OPLOCK"

#: Extra fields sent with the unlock/lock command.
CMD_FIELD_ACTION = "Action"
ACTION_LOCK = 0
ACTION_UNLOCK = 1

ATTR_RAW_PROPS = "raw_props"

# Fired on the HA bus for every new unlock record.
EVENT_UNLOCK = f"{DOMAIN}_unlock"
#: any lock notification from /v3/message/getNotifyLogList (alarm, door open, ...)
EVENT_NOTIFICATION = f"{DOMAIN}_notification"

# MQTT push (optional, off by default)
CONF_MQTT_ENABLED = "mqtt_enabled"
CONF_MQTT_HOST = "mqtt_host"
CONF_MQTT_PORT = "mqtt_port"
CONF_MQTT_TLS = "mqtt_tls"
CONF_MQTT_USERNAME = "mqtt_username"
CONF_MQTT_PASSWORD = "mqtt_password"
CONF_MQTT_TOPICS = "mqtt_topics"
CONF_MQTT_LOG_PAYLOADS = "mqtt_log_payloads"

CONF_RECORD_INTERVAL = "record_interval"
CONF_ALERT_RESET = "alert_reset"

#: seconds a notification-driven alert stays on before auto-clearing (0 = sticky)
DEFAULT_ALERT_RESET = 300
