"""Scriptable real-time offline transport. No SIP or codec simulation claims."""

from __future__ import annotations

import math
import struct
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field

from .metrics import rms
from .transport import Event


def tone(seconds: float, sample_rate: int = 8000, frequency: float = 440) -> bytes:
    return b"".join(
        struct.pack("<h", int(6000 * math.sin(2 * math.pi * frequency * i / sample_rate)))
        for i in range(int(seconds * sample_rate))
    )


@dataclass
class MockTransport:
    answer_after: float | None = 0.02
    greeting: bytes = field(default_factory=lambda: tone(0.16))
    responses: tuple[bytes, ...] = field(default_factory=lambda: (tone(0.12),))
    response_delay: float = 0.04
    first_audio_delay: float = 0.02
    stop_on_barge_in: bool = True
    yield_delay: float = 0.02
    sample_rate: int = 8000
    remote_hangup_after: float | None = None
    abnormal_hangup: bool = False
    dtmf_response: bytes = field(default_factory=lambda: tone(0.12, frequency=660))
    last_handle: MockCallHandle | None = field(default=None, init=False)

    def call(self, uri: str, transport: str = "udp") -> MockCallHandle:
        self.last_handle = MockCallHandle(self)
        return self.last_handle


class MockCallHandle:
    def __init__(self, config: MockTransport):
        self.config = config
        self.sample_rate = config.sample_rate
        self.started = time.monotonic()
        self._events = [Event("dialed", self.started), Event("ringing", self.started)]
        self._lock = threading.RLock()
        self._closed = threading.Event()
        self._queue: list[tuple[float, bytes]] = []
        self._response = 0
        self.digits: list[str] = []
        self._greeted = False

    def _update(self) -> None:
        now = time.monotonic()
        if (
            self.config.answer_after is not None
            and not self._closed.is_set()
            and now >= self.started + self.config.answer_after
        ):
            if not any(e.name == "answered" for e in self._events):
                at = self.started + self.config.answer_after
                self._events.append(Event("answered", at))
        if self.config.remote_hangup_after is not None:
            if now >= self.started + self.config.remote_hangup_after and not self._closed.is_set():
                name = "hangup_abnormal" if self.config.abnormal_hangup else "hangup_clean"
                self._events.append(Event(name, now))
                self._closed.set()

    @property
    def ladder(self) -> tuple[Event, ...]:
        with self._lock:
            self._update()
            return tuple(self._events)

    @property
    def answered(self) -> bool:
        return any(e.name == "answered" for e in self.ladder)

    def recv_audio(self) -> Iterator[bytes]:
        size = self.sample_rate // 50 * 2
        while not self._closed.is_set():
            with self._lock:
                self._update()
                if self._closed.is_set():
                    break
                if self.answered and not self._greeted:
                    self._queue.append(
                        (time.monotonic() + self.config.first_audio_delay, self.config.greeting)
                    )
                    self._greeted = True
                chunk = bytes(size)
                if self._queue and time.monotonic() >= self._queue[0][0]:
                    at, pcm = self._queue.pop(0)
                    chunk = pcm[:size].ljust(size, b"\0")
                    if len(pcm) > size:
                        self._queue.insert(0, (at, pcm[size:]))
            if self._closed.wait(0.02):
                break
            yield chunk

    def send_audio(self, pcm: bytes) -> None:
        if self._closed.is_set() or not self.answered:
            raise RuntimeError("call is not active")
        frame_bytes = self.sample_rate // 50 * 2
        interrupted = False
        for offset in range(0, len(pcm), frame_bytes):
            chunk = pcm[offset : offset + frame_bytes]
            if not interrupted and self.config.stop_on_barge_in and rms(chunk) >= 0.01:
                with self._lock:
                    if self._queue and self._queue[0][0] <= time.monotonic():
                        at, current = self._queue[0]
                        keep = int(self.config.yield_delay * self.sample_rate) * 2
                        self._queue = [(at, current[:keep])] if keep else []
                interrupted = True
            if self._closed.wait(len(chunk) / (2 * self.sample_rate)):
                raise RuntimeError("call ended during playback")
        with self._lock:
            if self._response < len(self.config.responses):
                self._queue.append(
                    (
                        time.monotonic() + self.config.response_delay,
                        self.config.responses[self._response],
                    )
                )
                self._response += 1

    def send_dtmf(self, digits: str) -> None:
        if not self.answered or self._closed.is_set():
            raise RuntimeError("call is not active")
        with self._lock:
            self.digits.append(digits)
            self._queue.append(
                (time.monotonic() + self.config.response_delay, self.config.dtmf_response)
            )

    def hangup(self) -> None:
        with self._lock:
            self._update()
            if not self._closed.is_set():
                self._events.append(Event("hangup_clean", time.monotonic()))
                self._closed.set()
