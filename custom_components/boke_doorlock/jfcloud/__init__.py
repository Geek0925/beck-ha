"""博克 (BOKE) / 杰峰 (JFTech) Wi-Fi 门锁云 SDK.

Extracted from ``com.jf.jlinkiot`` 2.5.1 (iCSee Home, formerly JLink) and the
official open-platform specification. Standalone usage::

    from jfcloud import JFCloudClient, LockStatus

    client = JFCloudClient(uuid="...", app_key="...", app_secret="...")
    devices = await client.list_devices()
    status = await client.get_prop_status(["SN123"])
"""
from .client import ENDPOINTS, HOSTS, JFCloudClient
from .defaults import APP_KEY, APP_SECRET
from .errors import JFCloudAuthError, JFCloudConnectionError, JFCloudError
from .models import ALIASES, LockDevice, LockStatus
from .records import OpenRecord, normalise_method
from .push import JFPushListener, flatten_payload
from .signature import SignatureGenerator, VERIFIED_VECTOR, make_signature

__all__ = [
    "ALIASES",
    "APP_KEY",
    "APP_SECRET",
    "ENDPOINTS",
    "HOSTS",
    "JFCloudAuthError",
    "JFCloudClient",
    "JFCloudConnectionError",
    "JFCloudError",
    "JFPushListener",
    "flatten_payload",
    "LockDevice",
    "LockStatus",
    "OpenRecord",
    "normalise_method",
    "SignatureGenerator",
    "VERIFIED_VECTOR",
    "make_signature",
]
