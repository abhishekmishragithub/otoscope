"""Explicitly opted-in smoke test; never runs during normal offline CI."""

import pytest


@pytest.mark.live
def test_real_answer_and_audio(call, otoscope_target):
    call.dial(otoscope_target, transport="tcp")
    assert call.answered_within(10)
    assert call.first_audio_within(5)
