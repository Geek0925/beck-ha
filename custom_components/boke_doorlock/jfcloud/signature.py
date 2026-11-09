"""JF Cloud (杰峰/博克 iCSee Home) request signature.

Extracted from the app's cloud protocol and cross-checked against the official
open-platform document (xmeye/openplatform-docs, "DevelopersMustRead -
Protocol specifications"):

    https://<host>/<code>/<version>/<timeMillis>/<signature>.rs

    signature = md5( merge( raw_bytes, shifted_bytes ) )
    raw_bytes     = (uuid + appKey + appSecret + timeMillis).encode("iso-8859-1")
    shifted_bytes = in-place "moved card" swap of raw_bytes
    merge         = [raw[0..n-1], shifted[n-1..0]] interleaved from both ends

The implementation is verified against the published vector:

    uuid=test, appKey=test, appSecret=password,
    timeMillis=00000011461748332239, movedCard=5
    -> 285a38b2ebf8787e42f047e0b711297b
"""
from __future__ import annotations

import hashlib
import threading
import time

__all__ = [
    "SignatureGenerator",
    "build_time_millis",
    "make_signature",
    "VERIFIED_VECTOR",
]

#: Published reference vector (uuid, app_key, app_secret, time_millis, moved_card, signature)
VERIFIED_VECTOR = (
    "test",
    "test",
    "password",
    "00000011461748332239",
    5,
    "285a38b2ebf8787e42f047e0b711297b",
)


def _shift(data: bytearray, moved_card: int) -> bytearray:
    """In-place "simple shift" used by the vendor's SignatureUtil.change()."""
    n = len(data)
    for i in range(n):
        j = n - (i + 1)
        temp = data[i] if (i % moved_card) > ((n - i) % moved_card) else data[j]
        data[i] = data[j]
        data[j] = temp
    return data


def _merge(raw: bytes, shifted: bytes) -> bytes:
    """Interleave raw from the front and shifted from the back."""
    n = len(raw)
    out = bytearray(n * 2)
    for i in range(n):
        out[i] = raw[i]
        out[n * 2 - 1 - i] = shifted[i]
    return bytes(out)


def make_signature(
    uuid: str,
    app_key: str,
    app_secret: str,
    time_millis: str,
    moved_card: int = 2,
) -> str:
    """Return the lowercase MD5 signature for one request."""
    if moved_card <= 0:
        raise ValueError("moved_card must be > 0")
    combined = f"{uuid}{app_key}{app_secret}{time_millis}"
    raw = combined.encode("iso-8859-1")
    shifted = _shift(bytearray(raw), moved_card)
    return hashlib.md5(_merge(raw, bytes(shifted))).hexdigest()


def build_time_millis(counter: int) -> str:
    """counter is 6 digits (zero padded) followed by 13-digit epoch millis."""
    return f"{counter:06d}{int(time.time() * 1000)}"


class SignatureGenerator:
    """Thread-safe monotonic counter + signature factory.

    The cloud rejects reused timestamps, so a per-process counter is appended in
    front of the millisecond clock exactly like the vendor SDK does.
    """

    def __init__(
        self,
        uuid: str,
        app_key: str,
        app_secret: str,
        moved_card: int = 2,
    ) -> None:
        self.uuid = uuid
        self.app_key = app_key
        self.app_secret = app_secret
        self.moved_card = moved_card
        self._counter = 0
        self._lock = threading.Lock()

    def next(self) -> tuple[str, str]:
        """Return ``(time_millis, signature)`` for the next request."""
        with self._lock:
            self._counter = self._counter + 1 if self._counter < 9_999_999 else 1
            counter = self._counter
        time_millis = build_time_millis(counter)
        return time_millis, make_signature(
            self.uuid,
            self.app_key,
            self.app_secret,
            time_millis,
            self.moved_card,
        )
