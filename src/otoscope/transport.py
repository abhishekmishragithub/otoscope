"""Transport boundary. Audio is paced, mono PCM16LE at handle.sample_rate.

call must return a handle promptly; signaling proceeds asynchronously.
recv_audio yields bounded frames (recommended 20 ms), including silence. It blocks
until media or termination. hangup must unblock it. send_audio blocks until the PCM
has been sent at real-time speed. answered and ladder must be safe to read while
receiving. Ladder times use time.monotonic(), with sanitized generic event names.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class Event:
    name: str
    at: float


@runtime_checkable
class CallHandle(Protocol):
    sample_rate: int

    @property
    def answered(self) -> bool: ...

    @property
    def ladder(self) -> Sequence[Event]: ...

    def send_audio(self, pcm: bytes) -> None: ...
    def recv_audio(self) -> Iterator[bytes]: ...
    def send_dtmf(self, digits: str) -> None: ...
    def hangup(self) -> None: ...


@runtime_checkable
class Transport(Protocol):
    def call(self, uri: str, transport: str = "udp") -> CallHandle: ...
