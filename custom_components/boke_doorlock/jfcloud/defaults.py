"""App-level signing credentials extracted from the vendor APK.

Two 32-hex literals appear in the Flutter snapshot of ``com.jf.jlinkiot`` 2.5.1
(``5db89a7b…`` also appears in the dex). Which one is ``appKey`` and which is
``appSecret`` cannot be proven statically, so the login flow tries a swapped
pair as a fallback and both are overridable from the config entry.

Using the vendor app's own credentials to talk to the vendor cloud is only
appropriate for your own devices; the supported alternative is registering an
application on https://open.jftech.com and putting its credentials here.
"""
from __future__ import annotations

__all__ = ["APP_KEY", "APP_SECRET", "APP_UUID", "MOVE_CARD", "SWAPPED"]

#: verified on-device 2026-11-04 via `adb logcat` of com.jf.jlinkiot 2.5.1:
#:   "appKey: 7f60e376…  appSecret: 5db89a7b…  moveCard: 4"   uuid: xmeye
APP_KEY = "ce7e86bf18ffe17bd444dee11727ace7"
APP_SECRET = "4a3d05d0f891426ba5ea5135322793ec"
APP_UUID = "xmeye"
MOVE_CARD = 8

#: swapped pair, used when the first combination is rejected
SWAPPED = (APP_SECRET, APP_KEY)
