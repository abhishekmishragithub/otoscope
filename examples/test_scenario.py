"""Run with pytest examples/ --otoscope-report=.otoscope-demo (fully offline)."""

from otoscope import CallSession, persona
from otoscope.mock import tone


@persona(voice="user-provided-wav-directory", language="en-IN")
def test_greeting_and_dtmf(call: CallSession):
    call.dial("sip:offline", transport="tcp")
    assert call.answered_within(1)
    assert call.first_audio_within(1)
    assert call.listen(0.15, timeout=2).duration > 0
    call.play(tone(0.12))
    reply = call.listen(0.15, timeout=2)
    call.send_dtmf("1#")
    assert call.listen(0.15, timeout=2).pcm != reply.pcm
    gap = call.metrics().turn_gap_p95
    assert gap is not None and gap < 0.5
