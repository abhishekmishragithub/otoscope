"""Opt-in by tool availability: actual SIP/RTP, no MockTransport replacement.

The sibling fixture stays a test/example tool, never an otoscope dependency.
"""

import shutil
import sys
import time
from pathlib import Path

import pytest

from otoscope import CallSession
from otoscope.sipling import SiplingTransport
from otoscope.transport import CallHandle

FIXTURES = Path(__file__).resolve().parents[2] / "sipling" / "examples"
pytestmark = pytest.mark.skipif(
    not FIXTURES.is_dir() or not shutil.which("sipling-stream") or not shutil.which("pjsua"),
    reason="requires sibling sipling and separately built local pjproject tools",
)
sys.path.insert(0, str(FIXTURES))


@pytest.mark.parametrize("transport", ["tcp", "udp"])
def test_real_conversation_and_reports(transport, tmp_path):
    pytest.importorskip("sipling")
    from local_agent import LocalAgent, tone

    with LocalAgent(transport) as agent:
        with CallSession(SiplingTransport(), max_duration=15) as session:
            before = time.monotonic()
            session.dial(agent.uri, transport)
            assert time.monotonic() - before < 0.15
            assert isinstance(session.handle, CallHandle)
            assert session.ringing_within(3)
            assert session.answered_within(3)
            assert session.first_audio_within(1)
            assert session.listen(0.12, timeout=2).duration >= 0.12
            agent.greeting(False)
            time.sleep(0.15)
            session.play(tone(0.3))
            # This is a commanded reply, not an agent recognition measurement.
            agent.greeting(True)
            assert session.listen(0.12, timeout=2).duration >= 0.12
            gap = session.metrics().turn_gap_p95
            assert gap is not None and 0 <= gap < 1
            session.send_dtmf("12#")
            deadline = time.monotonic() + 3
            while len(agent.dtmf) < 3 and time.monotonic() < deadline:
                time.sleep(0.02)
            assert agent.dtmf == ("1", "2", "#")
        assert session.metrics().hangup == "hangup_clean"
        evidence = session.artifacts(tmp_path)
        assert evidence["sample_rate"] == 8000
        assert "local-agent" not in str(evidence)


def test_real_silence_and_remote_bye():
    pytest.importorskip("sipling")
    from local_agent import LocalAgent

    with LocalAgent(greeting_seconds=0) as agent, CallSession(SiplingTransport()) as session:
        session.dial(agent.uri)
        assert session.answered_within(3)
        assert not session.first_audio_within(0.3)
        with pytest.raises(TimeoutError):
            session.listen(0.06, timeout=0.15)
        agent.command("h")
        assert session.hung_up_within(2)


def test_real_non_yielding_agent_rejects_barge_in():
    pytest.importorskip("sipling")
    from local_agent import LocalAgent, tone

    with LocalAgent(greeting_seconds=1.2) as agent, CallSession(SiplingTransport()) as session:
        session.dial(agent.uri)
        assert session.first_audio_within(3)
        time.sleep(0.04)
        assert not session.barge_in(tone(0.1), within_ms=140, silence=0.06)
        assert not session.metrics().barge_in_latencies
