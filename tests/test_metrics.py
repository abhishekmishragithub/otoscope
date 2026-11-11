import struct

import pytest

from otoscope.metrics import (
    EnergyFrame,
    barge_in_latency,
    percentile,
    rms,
    speech_segments,
    turn_gaps,
)
from otoscope.stt import word_error_rate


def test_energy():
    assert rms(bytes(100)) == 0
    assert rms(struct.pack("<hh", -16384, 16384)) == 0.5
    assert rms(struct.pack("<h", -32768)) == 1
    with pytest.raises(ValueError):
        rms(b"x")


def test_segments_and_gaps():
    frames = [
        EnergyFrame(0, 0.02, 0.2),
        EnergyFrame(0.02, 0.02, 0),
        EnergyFrame(0.04, 0.02, 0.2),
        EnergyFrame(1, 0.02, 0.3),
    ]
    assert speech_segments(frames) == [(0, 0.06), (1, 1.02)]
    assert turn_gaps([(0, 1), (3, 4)], [(1.2, 2), (3.8, 5)]) == pytest.approx([0.2, -0.2])
    assert turn_gaps([(0, 1), (2, 3)], [(4, 5)]) == [1]
    assert barge_in_latency(0.5, [(0, 1)]) == 0.5
    assert barge_in_latency(2, [(0, 1)]) is None
    assert percentile([0, 1, 2], 0.95) == 1.9
    assert percentile([], 0.95) is None


def test_wer():
    assert word_error_rate("hello world", "Hello world") == 0
    assert word_error_rate("one two three", "one four") == pytest.approx(2 / 3)
    assert word_error_rate("a", "a b c") == 2
    with pytest.raises(ValueError):
        word_error_rate("", "")
