"""Energy measurements for mono signed little-endian PCM16 (no cloud services)."""

from __future__ import annotations

import math
import struct
from collections.abc import Sequence
from dataclasses import dataclass


def rms(pcm: bytes) -> float:
    """Normalized RMS in [0, 1]. Reject partial samples."""
    if len(pcm) % 2:
        raise ValueError("PCM16 must contain complete samples")
    if not pcm:
        return 0.0
    values = struct.unpack(f"<{len(pcm) // 2}h", pcm)
    return math.sqrt(sum(v * v for v in values) / len(values)) / 32768


def percentile(values: Sequence[float], fraction: float) -> float | None:
    if not 0 <= fraction <= 1:
        raise ValueError("fraction must be between zero and one")
    if not values:
        return None
    ordered = sorted(values)
    p = (len(ordered) - 1) * fraction
    lo = int(p)
    return ordered[lo] + (ordered[min(lo + 1, len(ordered) - 1)] - ordered[lo]) * (p - lo)


@dataclass(frozen=True)
class EnergyFrame:
    start: float
    duration: float
    energy: float

    @property
    def end(self) -> float:
        return self.start + self.duration


def speech_segments(
    frames: Sequence[EnergyFrame],
    threshold: float = 0.01,
    silence: float = 0.12,
) -> list[tuple[float, float]]:
    """Merge voiced frames separated by less than the silence hysteresis."""
    segments: list[tuple[float, float]] = []
    for frame in frames:
        if frame.energy < threshold:
            continue
        if segments and frame.start - segments[-1][1] < silence:
            segments[-1] = (segments[-1][0], max(frame.end, segments[-1][1]))
        else:
            segments.append((frame.start, frame.end))
    return segments


def turn_gaps(
    caller: Sequence[tuple[float, float]], agent: Sequence[tuple[float, float]]
) -> list[float]:
    """Match each caller turn with the first agent onset before the next caller turn.

    Negative values denote overlap. One agent segment is consumed at most once.
    """
    gaps: list[float] = []
    used: set[int] = set()
    for index, (start, end) in enumerate(caller):
        next_start = caller[index + 1][0] if index + 1 < len(caller) else math.inf
        for i, (agent_start, _) in enumerate(agent):
            if i not in used and start <= agent_start < next_start:
                gaps.append(agent_start - end)
                used.add(i)
                break
    return gaps


def barge_in_latency(
    caller_start: float,
    agent: Sequence[tuple[float, float]],
) -> float | None:
    """Return time until the overlapping agent segment stops, or None if no overlap."""
    for start, end in agent:
        if start <= caller_start < end:
            return end - caller_start
    return None


@dataclass(frozen=True)
class Metrics:
    ringing_seconds: float | None
    answer_seconds: float | None
    first_audio_seconds: float | None
    turn_gaps: tuple[float, ...]
    turn_gap_p95: float | None
    barge_in_latencies: tuple[float, ...]
    hangup: str | None
