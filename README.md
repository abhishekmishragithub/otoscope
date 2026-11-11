# Otoscope (working name)

**CI for what a caller actually experiences.** Dial a real SIP endpoint, play caller
turns, capture the agent's PCM, and catch regressions in pickup, first audio,
turn-taking, interruptions, and DTMF handling. Platform simulations skip the
telephony boundary; Otoscope delegates real SIP/RTP to **sipling**.

This is an early implementation with offline and real loopback SIP/RTP tests.
The mock remains a deterministic scripted transport. sipling v0.2 now satisfies
the unchanged adapter contract locally; no external endpoint has been verified.
See `../INTEGRATION.md` and run `sh ../sipling/examples/run-local.sh BUILD_DIR`
for the two-process local proof.

PyPI's `otoscope` name is already occupied by an unrelated VAD library (checked
2026-10-24 at <https://pypi.org/project/otoscope/>). This project provisionally uses
**otoscope**, with `from otoscope import ...`. Do not install both distributions
in one environment. The new distribution name is not reserved or published.

## Install and run offline

Python 3.10+, no runtime dependencies for the core:

```sh
python -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
pytest --otoscope-report=.otoscope --junitxml=.otoscope/junit.xml
ruff check .
mypy src/otoscope
python -m build
```

The `call` pytest fixture uses `MockTransport` by default. Tests marked `live` are
skipped without `--otoscope-live`.

```python
from otoscope import CallSession, persona
from otoscope.mock import tone


@persona(voice="wav_dir/polite_customer", language="en-IN")
def test_conversation(call: CallSession):
    call.dial("sip:offline", transport="tcp")
    assert call.answered_within(seconds=5)
    assert call.first_audio_within(seconds=3)
    greeting = call.listen(until_silence=0.12)
    call.play(tone(0.2))  # For real scenarios use a caller WAV or user-provided TTS PCM.
    reply = call.listen(until_silence=0.12)
    assert reply.duration > 0
    m = call.metrics()
    assert m.turn_gap_p95 is not None and m.turn_gap_p95 < 2.5
```

`play` accepts a WAV path or bytes. WAVs must be uncompressed mono PCM16LE at the
transport sample rate; bytes use the same format. No implicit resampling, bundled
TTS, cloud calls, or model downloads. `persona` attaches metadata to a function; it
does not synthesize a voice. Silence, leading/trailing padding and turn gaps are
measured using 20 ms PCM energy windows (normalized RMS threshold defaults to .01).
Tune the threshold for your audio environment. Silence timeouts need headroom in CI.

## Real calls: explicit opt-in

**Real PSTN calls cost money. Obtain consent and authorization for the destination
and recording before dialing.** No destination is bundled. Use a separate environment
for real calling. sipling is being developed separately and was not present on
PyPI at implementation time; do not assume `pip install .[sipling]` works yet.

Once a compatible, license-reviewed sipling build is available:

```sh
# Install the separately supplied compatible sipling build first.
export OTOSCOPE_TARGET_URI='sip:your-authorized-target@your-host'
pytest tests/test_live.py --otoscope-live --otoscope-report=.otoscope-live
```

Override `otoscope_transport` to provide your own transport. Direct use is also
supported: `with CallSession(SiplingTransport()) as call: ...`. See
[`transport.py`](src/otoscope/transport.py) for the Protocol. `call(uri, transport)`
returns a handle with `answered`, a snapshot `ladder`, `sample_rate`, paced
`send_audio(pcm)`, blocking `recv_audio()` yielding bounded PCM frames including
silence, `send_dtmf(digits)`, and `hangup()` which unblocks receive. Ladder events use
`time.monotonic()` timestamps and generic names. Transport implementations own SIP,
codecs, RTP jitter buffering, sample conversion and RFC2833/4733 event packets.
The thin adapter delegates this interface unchanged. sipling v0.2 supplies 8 kHz
PCM in 20 ms frames, normalized events and RFC4733; it requires a separately built
helper. It does not implement SIP in otoscope.

## Assertions and metrics

- `answered_within(seconds)` uses the deadline from dial; `first_audio_within`
  uses answer-to-first-voiced-frame, ignoring early media. These return booleans.
- `listen(until_silence=1.2, timeout=15)` waits for voiced audio then sustained
  silence, returning a `Reply` with PCM, duration and optional text. At timeout or
  remote hangup it returns the voiced portion received so far; it raises
  `TimeoutError` if no speech arrived. Duration includes internal pauses and excludes
  trailing silence. Receive failures propagate as errors.
- `play` records caller energy and `metrics()` matches caller turns to inbound
  speech onsets. Missing responses are omitted, not zero; assert a reply separately.
  P95 uses linear interpolation and is `None` without samples. Overlap is negative.
- `barge_in(wav_or_pcm, within_ms=500)` requires currently active agent energy,
  plays caller audio and requires sustained inbound silence (default 120 ms).
  It returns whether the measured stop latency from caller energy onset meets the bound. No active speech
  returns False. Latency is energy cessation, not semantic interruption recognition;
  background music/echo can invalidate it. Use interruption-specific scripts without
  an immediate next agent response.
- `send_dtmf('12#')` delegates RFC2833 sending to the transport. Capture a baseline
  reply, send digits, then `listen` and compare PCM or expected transcript. Different
  PCM alone cannot prove the agent understood the digits. The mock validates wiring,
  not packet encoding.
- `hung_up_within(seconds, clean=True)` waits from invocation. `metrics().hangup`
  records clean/abnormal transport evidence. A default 120-second watchdog hangs up
  stalled calls (`limit_reached`); override `max_duration` in direct sessions.
- `transferred_within` requires an explicit `transferred` transport event. Ringback,
  second-leg audio and hold music are ambiguous and aren't treated as proof.

Timing is observed at the PCM boundary on the caller host. SIP answer/ringing and
hangup timestamps necessarily come from signaling; all **audio** latency metrics
come from PCM energy. Scheduling, frame duration and the transport jitter buffer
bound precision; this is not a packet capture or a measurement of one-way network
latency. `agent.wav` is the continuous received stream; `caller.wav` concatenates
sent turns (see caller_energy timestamps for their placement). `turn-N.wav` contains
each consumed reply. Recordings and energy timelines allow comparison across runs.

## Reports and privacy

The pytest plugin emits `.otoscope/run.json` (schema version 1), a static one-page
`index.html` with turn-gap histogram, per-test signaling ladder and WAV players,
and per-test directories. Answer, first-audio, turn-gap and barge-in histograms show
sample counts (an empty histogram means no observations). Open the HTML locally. Pytest retains normal assertion
failures and JUnit compatibility. Failed tests with active sessions retain audio.
Use a distinct `--otoscope-report` directory for each run. Parallel xdist aggregation
is not supported yet; run voice tests serially.

Otoscope never stores/logs the dial URI and only serializes allowlisted event names.
Parametrized test names are redacted in Otoscope reports. Audio can itself contain
identities or sensitive conversation: protect artifacts and apply retention rules.
Transport logs, pytest tracebacks/JUnit, user test names, and third-party exceptions
are outside this redaction boundary: avoid literals containing credentials in tests
and configure transport logging appropriately.

## Optional local transcription and WER

```sh
pip install -e '.[stt]'
```

```python
from otoscope import CallSession
from otoscope.stt import LocalWhisper, word_error_rate
from otoscope.mock import MockTransport

stt = LocalWhisper("/models/faster-whisper-small", language="en")
with CallSession(MockTransport(), transcriber=stt) as call:
    # Dial/play/listen here with real speech WAVs, not synthetic tones.
    pass
# reply.text supports `assert "appointment" in (reply.text or "").lower()`.
# reply.wer(known_reference) computes WER and includes the score in session artifacts.
# word_error_rate(known_reference, reply.text) also works as a standalone function.
```

The STT adapter rejects FFmpeg builds labeled GPL, nonfree or unknown. Pip may still
install those binaries before this runtime check; provision and audit an LGPL-only
PyAV/FFmpeg build before installing optional dependencies for redistribution.

Pre-provision a compatible faster-whisper model directory. Model-name downloads
are rejected; `local_files_only=True` is enforced. WER case-folds and splits on
whitespace, and does not normalize punctuation. WER can exceed 1. A known spoken
reference is required; STT accuracy and language choice confound path quality.
It is not a codec quality score. To assess caller-to-agent recognition, obtain
agent-side transcription through a separately authorized interface: inbound agent
recordings alone cannot measure what the agent heard.

## License and limitations

Core is MIT and uses only the Python standard library. Optional dependencies are
not vendored. See [DEPENDENCIES.md](DEPENDENCIES.md) for license review and the
unresolved sipling release gate. Do not redistribute bundled binaries before
checking their actual build licenses.

This tool does **not** measure perceptual quality/MOS, prove transfer completion
from audio, provide ASR ground truth, or simulate carrier impairments. Reference
quality methods include ITU-T POLQA (P.863), PESQ (P.862) and controlled listening
studies; their licensing and validity conditions require separate consideration.

Roadmap: integrate and verify real sipling over UDP/TCP/TLS and supported codecs;
validate RFC2833 with packet capture; add labeled transfer evidence and jitter/loss
metadata; baseline comparisons and run-to-run audio diffs; more robust double-talk
and echo discrimination; xdist reporting; multi-rate and long-call stress tests.
No PyPI publication, remote push or live call was performed for this launch.
