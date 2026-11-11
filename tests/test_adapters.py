import io
import types
import wave

import pytest

from otoscope.mock import MockTransport, tone
from otoscope.sipling import SiplingTransport
from otoscope.stt import LocalWhisper


def test_sipling_delegates_contract(monkeypatch):
    transport = MockTransport()
    calls = []

    def dial(uri, transport="udp"):
        calls.append((uri, transport))
        return mock.call(uri, transport)

    mock = transport
    monkeypatch.setitem(__import__("sys").modules, "sipling", types.SimpleNamespace(call=dial))
    handle = SiplingTransport().call("sip:local", "tls")
    try:
        assert calls == [("sip:local", "tls")]
        assert handle.sample_rate == 8000
    finally:
        handle.hangup()


def test_stt_local_only_and_pcm_wav(monkeypatch, tmp_path):
    from otoscope import stt

    captured = {}

    class Model:
        def __init__(self, directory, **kwargs):
            captured.update(directory=directory, **kwargs)

        def transcribe(self, stream, language):
            with wave.open(io.BytesIO(stream.read())) as wav:
                assert wav.getframerate() == 8000
                assert wav.readframes(800) == tone(0.1)
            assert language == "en"
            return [types.SimpleNamespace(text=" appointment ")], None

    def import_module(name):
        if name == "av._core":
            return types.SimpleNamespace(
                library_meta={
                    "libavcodec": {"license": b"LGPL version 3 or later", "configuration": b""}
                }
            )
        assert name == "faster_whisper"
        return types.SimpleNamespace(WhisperModel=Model)

    monkeypatch.setattr(stt.importlib, "import_module", import_module)
    transcribe = LocalWhisper(tmp_path, language="en")
    assert captured["local_files_only"] is True
    assert transcribe(tone(0.1), 8000) == "appointment"
    with pytest.raises(ValueError, match="pre-downloaded"):
        LocalWhisper(tmp_path / "absent")


@pytest.mark.parametrize(
    "license_name,configuration",
    [
        ("GPL version 3", ""),
        ("unknown", ""),
        ("LGPL version 3", "--enable-gpl"),
        ("LGPL version 3", "--enable-nonfree"),
    ],
)
def test_stt_rejects_gpl_or_unknown_ffmpeg(monkeypatch, tmp_path, license_name, configuration):
    from otoscope import stt

    def import_module(name):
        assert name == "av._core"  # Must refuse before importing faster-whisper.
        return types.SimpleNamespace(
            library_meta={"libavcodec": {"license": license_name, "configuration": configuration}}
        )

    monkeypatch.setattr(stt.importlib, "import_module", import_module)
    with pytest.raises(RuntimeError, match="LGPL-only"):
        LocalWhisper(tmp_path)
