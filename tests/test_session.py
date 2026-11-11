import json
import time
import wave

import pytest

from otoscope import CallSession, persona
from otoscope.mock import MockTransport, tone
from otoscope.report import write_report


def test_conversation_and_artifacts(tmp_path):
    with CallSession(MockTransport()) as call:
        call.dial("sip:secret:password@example.test", transport="tcp")
        assert call.answered_within(0.3)
        assert call.first_audio_within(0.3)
        assert call.listen(0.06, timeout=1).duration >= 0.12
        call.play(tone(0.1))
        reply = call.listen(0.06, timeout=1)
        assert reply.duration >= 0.1
        metrics = call.metrics()
        assert metrics.turn_gap_p95 is not None
        assert 0 <= metrics.turn_gap_p95 < 0.3
        call.send_dtmf("12#")
        dtmf = call.listen(0.06, timeout=1)
        assert dtmf.pcm != reply.pcm
        assert call.transport.last_handle.digits == ["12#"]
        with pytest.raises(ValueError):
            call.send_dtmf("oops")
    evidence = call.artifacts(tmp_path / "test")
    evidence.update(test="<script>alert(1)</script>", outcome="passed", artifact_id="test")
    write_report(tmp_path, [evidence])
    data = (tmp_path / "run.json").read_text()
    assert "secret" not in data and "example.test" not in data
    assert json.loads(data)["schema_version"] == 1
    assert "&lt;script&gt;" in (tmp_path / "index.html").read_text()
    with wave.open(str(tmp_path / "test/agent.wav")) as wav:
        assert wav.getnframes() > 0
    assert call.metrics().hangup == "hangup_clean"


@pytest.mark.parametrize("yields,expected", [(True, True), (False, False)])
def test_barge_in(yields, expected):
    mock = MockTransport(greeting=tone(0.8), responses=(), stop_on_barge_in=yields)
    with CallSession(mock) as call:
        call.dial("sip:offline")
        assert call.answered_within(0.3)
        assert call.first_audio_within(0.3)
        time.sleep(0.04)
        assert call.barge_in(tone(0.08), within_ms=140, silence=0.06) is expected
        if expected:
            assert call.metrics().barge_in_latencies[0] < 0.14


def test_silence_not_barge_in_success():
    with CallSession(MockTransport(greeting=b"", responses=())) as call:
        call.dial("sip:offline")
        assert call.answered_within(0.3)
        assert not call.first_audio_within(0.06)
        assert not call.barge_in(tone(0.1))
        with pytest.raises(TimeoutError):
            call.listen(0.02, timeout=0.04)


def test_answer_deadline_and_max_duration():
    with CallSession(MockTransport(answer_after=None), max_duration=0.12) as call:
        call.dial("sip:offline")
        assert not call.answered_within(0.04)
        assert call.hung_up_within(0.3)
        assert call.limit_reached
    with CallSession(MockTransport(answer_after=0.06)) as call:
        call.dial("sip:offline")
        time.sleep(0.1)
        assert not call.answered_within(0.03)
        assert call.answered_within(0.2)


@pytest.mark.parametrize("abnormal", [False, True])
def test_remote_hangup(abnormal):
    with CallSession(MockTransport(remote_hangup_after=0.08, abnormal_hangup=abnormal)) as call:
        call.dial("sip:offline")
        assert call.hung_up_within(0.4, clean=not abnormal)
        assert not call.transferred_within(0.01)


def test_validation_and_receive_failure(tmp_path):
    call = CallSession(MockTransport())
    with pytest.raises(RuntimeError):
        call.play(tone(0.1))
    with pytest.raises(ValueError):
        call.dial("sip:offline", transport="invalid")
    with call:
        call.dial("sip:offline")
        assert call.answered_within(0.3)
        with pytest.raises(RuntimeError):
            call.dial("sip:offline")
        with pytest.raises(ValueError):
            call.play(b"1")
        wav_path = tmp_path / "wrong.wav"
        with wave.open(str(wav_path), "wb") as wav:
            wav.setparams((2, 2, 8000, 0, "NONE", "none"))
            wav.writeframes(tone(0.1))
        with pytest.raises(ValueError):
            call.play(wav_path)

    class BrokenHandle:
        sample_rate = 8000
        ladder = ()
        answered = False

        def recv_audio(self):
            raise OSError("transport failed")
            yield b""

        def hangup(self):
            pass

    class BrokenTransport:
        def call(self, uri, transport="udp"):
            return BrokenHandle()

    with CallSession(BrokenTransport()) as broken:
        broken.dial("sip:offline")
        with pytest.raises(RuntimeError, match="audio receive failed"):
            broken.answered_within(0.2)


def test_persona():
    @persona(voice="local", language="en-IN")
    def scenario():
        pass

    assert scenario.otoscope_persona["language"] == "en-IN"


def test_wav_playback_trims_caller_padding(tmp_path):
    with CallSession(MockTransport()) as call:
        call.dial("sip:offline")
        assert call.dialed
        assert call.ringing_within(0.2)
        assert call.answered_within(0.3)
        call.listen(0.14, timeout=1)
        path = tmp_path / "caller.wav"
        with wave.open(str(path), "wb") as wav:
            wav.setparams((1, 2, 8000, 0, "NONE", "none"))
            wav.writeframes(bytes(800) + tone(0.08) + bytes(800))
        call.play(path)
        call.listen(0.06, timeout=1)
        # Response latency includes the caller WAV's trailing 50 ms silence.
        gap = call.metrics().turn_gap_p95
        assert gap is not None and 0.05 <= gap < 0.3


def test_barge_in_detects_cessation_during_long_playback():
    with CallSession(MockTransport(greeting=tone(1), responses=())) as call:
        call.dial("sip:offline")
        assert call.first_audio_within(0.3)
        time.sleep(0.04)
        assert call.barge_in(tone(0.3), within_ms=100, silence=0.06)


def test_barge_in_uses_caller_energy_onset_and_rejects_silent_input():
    with CallSession(MockTransport(greeting=tone(1.5), responses=())) as call:
        call.dial("sip:offline")
        assert call.first_audio_within(0.3)
        time.sleep(0.04)
        assert call.barge_in(bytes(1600) + tone(0.15), within_ms=100, silence=0.06)
        assert call.metrics().barge_in_latencies[0] < 0.1
    with CallSession(MockTransport(greeting=tone(0.6), responses=())) as call:
        call.dial("sip:offline")
        assert call.first_audio_within(0.3)
        assert not call.barge_in(bytes(1600), within_ms=100, silence=0.06)


def test_transcript_wer_in_artifacts(tmp_path):
    with CallSession(
        MockTransport(), transcriber=lambda pcm, rate: "appointment confirmed"
    ) as call:
        call.dial("sip:offline")
        reply = call.listen(0.06, timeout=1)
        assert reply.text == "appointment confirmed"
        assert reply.wer("appointment confirmed") == 0
    evidence = call.artifacts(tmp_path)
    assert evidence["turns"][0]["wer"] == 0
    assert "appointment" not in json.dumps(evidence)  # No transcript written by default.


def test_max_duration_prevents_late_answer():
    with CallSession(MockTransport(answer_after=0.12), max_duration=0.04) as call:
        call.dial("sip:offline")
        assert call.hung_up_within(0.3)
        time.sleep(0.13)
        assert not call.answered_within(0.5)
        assert call.metrics().answer_seconds is None
