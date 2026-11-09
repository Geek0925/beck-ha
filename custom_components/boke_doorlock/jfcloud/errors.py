"""Exceptions raised by the JF cloud client."""
from __future__ import annotations

from typing import Any


class JFCloudError(Exception):
    """Base error for any cloud-side failure."""

    def __init__(self, message: str, *, code: int | None = None, payload: Any = None) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.payload = payload

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.message} (code={self.code})" if self.code is not None else self.message


class JFCloudAuthError(JFCloudError):
    """Credentials (uuid / appKey / appSecret) were rejected."""


class JFCloudConnectionError(JFCloudError):
    """Transport failure: DNS, TCP, TLS or timeout."""
