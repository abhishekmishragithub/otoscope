"""Thin adapter for the separately developed sipling contract."""

from __future__ import annotations

import importlib
from typing import cast

from .transport import CallHandle


class SiplingTransport:
    def call(self, uri: str, transport: str = "udp") -> CallHandle:
        try:
            module = importlib.import_module("sipling")
        except ImportError as exc:
            raise ImportError(
                "Install a compatible, license-reviewed otoscope[sipling]"
            ) from exc
        # sipling must implement PCM pacing, codec conversion, RFC2833 and the
        # normalized Event/monotonic clock contract documented in transport.py.
        return cast(CallHandle, module.call(uri, transport=transport))
