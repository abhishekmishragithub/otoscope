from __future__ import annotations

import threading
import time
import wave
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from .metrics import EnergyFrame, Metrics, percentile, rms, speech_segments, turn_gaps
from .transport import CallHandle, Transport

F = TypeVar("F", bound=Callable[..., Any])


def persona(*, voice: str, language: str = "en") -> Callable[[F], F]:
    """Attach caller metadata; does not synthesize audio or select a bundled TTS."""

    def decorate(fn: F) -> F:
        fn.otoscope_persona = {"voice": voice, "language": language}  # type: ignore[attr-defined]
        return fn

    return decorate


@dataclass
class Reply:
    pcm: bytes
    sample_rate: int
    start: float
    text: str | None = None
    reference_wer: float | None = None

    def wer(self, reference: str) -> float:
        """Score a known prompt against optional STT; retain the score for reports."""
        from .stt import word_error_rate

        if self.text is None:
            raise ValueError("transcribe the reply before computing WER")
        self.reference_wer = word_error_rate(reference, self.text)
        return self.reference_wer

    @property
    def duration(self) -> float:
        return len(self.pcm) / (2 * self.sample_rate)


class CallSession:
    def __init__(
        self,
        transport: Transport,
        *,
        max_duration: float = 120,
        energy_threshold: float = 0.01,
        transcriber: Callable[[bytes, int], str] | None = None,
    ):
        if max_duration <= 0 or not 0 < energy_threshold < 1:
            raise ValueError("invalid duration or energy threshold")
        self.transport = transport
        self.max_duration = max_duration
        self.threshold = energy_threshold
        self.transcriber = transcriber
        self.handle: CallHandle | None = None
        self.started = 0.0
        self._frames: list[tuple[EnergyFrame, bytes]] = []
        self._caller: list[tuple[EnergyFrame, bytes]] = []
        self._turns: list[tuple[float, float]] = []
        self.replies: list[Reply] = []
        self._barge: list[float] = []
        self._cursor = 0
        self._condition = threading.Condition()
        self._reader: threading.Thread | None = None
        self._timer: threading.Timer | None = None
        self._error: BaseException | None = None
        self._ended = False
        self.limit_reached = False

    def __enter__(self) -> CallSession:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def dial(self, uri: str, transport: str = "udp") -> None:
        if self.handle is not None:
            raise RuntimeError("one call per session")
        if transport not in {"udp", "tcp", "tls"}:
            raise ValueError("transport must be udp, tcp or tls")
        self.started = time.monotonic()
        # Never retain or log the destination identity.
        self.handle = self.transport.call(uri, transport=transport)
        if self.handle.sample_rate <= 0:
            self.handle.hangup()
            raise ValueError("invalid transport sample rate")
        self._reader = threading.Thread(target=self._receive, daemon=True)
        self._reader.start()
        self._timer = threading.Timer(self.max_duration, self._expire)
        self._timer.daemon = True
        self._timer.start()

    def _active(self) -> CallHandle:
        if self.handle is None:
            raise RuntimeError("dial first")
        return self.handle

    def _receive(self) -> None:
        handle = self._active()
        try:
            for pcm in handle.recv_audio():
                duration = len(pcm) / (2 * handle.sample_rate)
                frame = EnergyFrame(time.monotonic() - self.started - duration, duration, rms(pcm))
                with self._condition:
                    self._frames.append((frame, pcm))
                    self._condition.notify_all()
        except BaseException as exc:
            with self._condition:
                self._error = exc
        finally:
            with self._condition:
                self._ended = True
                self._condition.notify_all()

    def _expire(self) -> None:
        self.limit_reached = True
        self._active().hangup()

    def _wait(self, predicate: Callable[[], bool], seconds: float) -> bool:
        if seconds < 0:
            raise ValueError("timeout must be nonnegative")
        deadline = time.monotonic() + seconds
        with self._condition:
            while True:
                if self._error is not None:
                    raise RuntimeError("audio receive failed") from self._error
                if predicate():
                    return True
                if self._ended or time.monotonic() >= deadline:
                    return False
                self._condition.wait(min(0.02, max(0, deadline - time.monotonic())))

    def _event_time(self, name: str) -> float | None:
        return next((e.at - self.started for e in self._active().ladder if e.name == name), None)

    @property
    def dialed(self) -> bool:
        return self.handle is not None

    def ringing_within(self, seconds: float) -> bool:
        self._active()
        return self._wait(
            lambda: (at := self._event_time("ringing")) is not None and at <= seconds,
            max(0, seconds - (time.monotonic() - self.started)),
        )

    def answered_within(self, seconds: float) -> bool:
        self._active()
        return self._wait(
            lambda: (at := self._event_time("answered")) is not None and at <= seconds,
            max(0, seconds - (time.monotonic() - self.started)),
        )

    def _first_audio(self) -> float | None:
        answered = self._event_time("answered")
        if answered is None:
            return None
        with self._condition:
            return next(
                (
                    max(0, f.start - answered)
                    for f, _ in self._frames
                    if f.energy >= self.threshold and f.end >= answered
                ),
                None,
            )

    def first_audio_within(self, seconds: float) -> bool:
        """Deadline is measured from answer, excluding early media."""
        self._active()
        if seconds < 0:
            raise ValueError("timeout must be nonnegative")
        if not self._wait(lambda: self._event_time("answered") is not None, seconds):
            return False
        answered = self._event_time("answered")
        assert answered is not None
        remaining = max(0, self.started + answered + seconds - time.monotonic())
        return self._wait(
            lambda: (at := self._first_audio()) is not None and at <= seconds,
            remaining,
        )

    def play(self, audio: str | Path | bytes) -> None:
        handle = self._active()
        if isinstance(audio, (str, Path)):
            with wave.open(str(audio), "rb") as wav:
                if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) != (
                    1,
                    2,
                    handle.sample_rate,
                ) or wav.getcomptype() != "NONE":
                    raise ValueError("WAV must be uncompressed mono PCM16 at transport sample rate")
                pcm = wav.readframes(wav.getnframes())
        else:
            pcm = audio
        rms(pcm)  # Validate even length before transmission.
        if not pcm:
            raise ValueError("empty caller turn")
        start = time.monotonic() - self.started
        with self._condition:
            self._cursor = len(self._frames)
        handle.send_audio(pcm)
        # The send contract guarantees real-time playback. Energy on the transmitted
        # waveform excludes leading/trailing silence from turn boundaries.
        size = max(2, handle.sample_rate // 50 * 2)
        frames = []
        for offset in range(0, len(pcm), size):
            chunk = pcm[offset : offset + size]
            frames.append(
                (
                    EnergyFrame(
                        start + offset / (2 * handle.sample_rate),
                        len(chunk) / (2 * handle.sample_rate),
                        rms(chunk),
                    ),
                    chunk,
                )
            )
        segments = speech_segments([f for f, _ in frames], self.threshold)
        with self._condition:
            self._caller.extend(frames)
            if segments:
                self._turns.append((segments[0][0], segments[-1][1]))

    def listen(self, until_silence: float = 1.2, *, timeout: float = 15) -> Reply:
        if until_silence <= 0 or timeout <= 0:
            raise ValueError("listen durations must be positive")
        self._active()
        first: int | None = None
        last: int | None = None
        scan = self._cursor

        def complete() -> bool:
            nonlocal first, last, scan
            while scan < len(self._frames):
                frame, _ = self._frames[scan]
                if frame.energy >= self.threshold:
                    if first is None:
                        first = scan
                    last = scan
                if last is not None and frame.end - self._frames[last][0].end >= until_silence:
                    scan += 1
                    return True
                scan += 1
            return False

        self._wait(complete, timeout)
        with self._condition:
            self._cursor = scan
            if first is None or last is None:
                raise TimeoutError("no agent audio before timeout or hangup")
            reply = Reply(
                b"".join(p for _, p in self._frames[first : last + 1]),
                self._active().sample_rate,
                self._frames[first][0].start,
            )
        if self.transcriber is not None:
            reply.text = self.transcriber(reply.pcm, reply.sample_rate)
        self.replies.append(reply)
        return reply

    def barge_in(
        self, audio: str | Path | bytes, *, within_ms: float = 500, silence: float = 0.12
    ) -> bool:
        """Speak over currently voiced inbound audio and require sustained silence.

        Returns False when no agent speech is active; silence is never a success.
        """
        if within_ms < 0 or silence <= 0:
            raise ValueError("invalid barge-in bounds")
        with self._condition:
            now = time.monotonic() - self.started
            if not self._frames or self._frames[-1][0].energy < self.threshold:
                return False
            if now - self._frames[-1][0].end > 0.1:
                return False
            index = len(self._frames)
            turn_count = len(self._turns)
        self.play(audio)
        if len(self._turns) == turn_count:
            return False
        onset = self._turns[-1][0]
        with self._condition:
            # Require overlapping voice at the caller's energy onset, including
            # leading WAV silence rather than measuring from file playback start.
            active = next((f for f, _ in reversed(self._frames) if f.start <= onset), None)
            if active is None or active.energy < self.threshold or onset - active.end > 0.04:
                return False
        stop: float | None = None

        def yielded() -> bool:
            nonlocal stop
            frames = [f for f, _ in self._frames[index:]]
            last_voice = onset
            for frame in frames:
                if frame.end < onset:
                    continue
                if frame.energy >= self.threshold:
                    last_voice = frame.end
                elif frame.end - last_voice >= silence:
                    stop = last_voice
                    return True
            return False

        remaining = max(0, self.started + onset + within_ms / 1000 + silence - time.monotonic())
        success = self._wait(yielded, remaining)
        if success and stop is not None:
            latency = max(0, stop - onset)
            self._barge.append(latency)
            return latency <= within_ms / 1000
        return False

    def send_dtmf(self, digits: str) -> None:
        if not digits or any(d not in "0123456789*#ABCD" for d in digits):
            raise ValueError("invalid RFC2833 digits")
        with self._condition:
            self._cursor = len(self._frames)
        self._active().send_dtmf(digits)

    def hung_up_within(self, seconds: float, *, clean: bool = True) -> bool:
        name = "hangup_clean" if clean else "hangup_abnormal"
        return self._wait(lambda: self._event_time(name) is not None, seconds)

    def transferred_within(self, seconds: float) -> bool:
        """Only explicit transport evidence; ringback/hold alone cannot prove transfer."""
        return self._wait(lambda: self._event_time("transferred") is not None, seconds)

    def metrics(self) -> Metrics:
        with self._condition:
            segments = speech_segments([f for f, _ in self._frames], self.threshold)
            gaps = turn_gaps(self._turns, segments)
        hangup = next(
            (
                e.name
                for e in self._active().ladder
                if e.name in {"hangup_clean", "hangup_abnormal"}
            ),
            None,
        )
        return Metrics(
            self._event_time("ringing"),
            self._event_time("answered"),
            self._first_audio(),
            tuple(gaps),
            percentile(gaps, 0.95),
            tuple(self._barge),
            hangup,
        )

    def close(self) -> None:
        if self._timer:
            self._timer.cancel()
        if self.handle:
            self.handle.hangup()
        if self._reader:
            self._reader.join(timeout=2)
            if self._reader.is_alive():
                raise RuntimeError("transport hangup did not unblock recv_audio")

    def artifacts(self, directory: Path) -> dict[str, Any]:
        """Write PCM WAVs and sanitized evidence. Call after close for a stable snapshot."""
        from dataclasses import asdict

        directory.mkdir(parents=True, exist_ok=True)
        handle = self._active()
        audio: list[str] = []
        streams = [
            ("agent.wav", b"".join(p for _, p in self._frames)),
            ("caller.wav", b"".join(p for _, p in self._caller)),
        ]
        streams.extend((f"turn-{i}.wav", r.pcm) for i, r in enumerate(self.replies))
        for name, pcm in streams:
            with wave.open(str(directory / name), "wb") as wav:
                wav.setparams((1, 2, handle.sample_rate, 0, "NONE", "not compressed"))
                wav.writeframes(pcm)
            audio.append(name)
        allowed = {
            "dialed",
            "ringing",
            "answered",
            "transferred",
            "hangup_clean",
            "hangup_abnormal",
        }
        return {
            "metrics": asdict(self.metrics()),
            "max_duration_reached": self.limit_reached,
            "ladder": [
                {"name": e.name, "seconds": e.at - self.started}
                for e in handle.ladder
                if e.name in allowed
            ],
            "audio": audio,
            "sample_rate": handle.sample_rate,
            "turns": [
                {"start": r.start, "duration": r.duration, "wer": r.reference_wer}
                for r in self.replies
            ],
            "agent_energy": [asdict(f) for f, _ in self._frames],
            "caller_energy": [asdict(f) for f, _ in self._caller],
        }
