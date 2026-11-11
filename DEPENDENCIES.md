# Dependency and license review

Review performed 2026-10-24 using read-only PyPI metadata and upstream source.
This repo's core is MIT, stdlib-only, and does not vendor dependencies or binaries.
Optional libraries are imported only on explicit use. Build artifacts contain
Otoscope code and license, not FFmpeg, STT models or SIP libraries.

| Component | Upstream license / decision |
| --- | --- |
| faster-whisper 1.2.1 | MIT; optional `[stt]` |
| CTranslate2 | MIT; transitive STT runtime |
| ONNX Runtime | MIT; transitive STT runtime |
| Hugging Face Hub / tokenizers | Apache-2.0; models must be pre-provisioned with their own license review |
| tqdm | MPL-2.0 and MIT; neither is GPL |
| PyAV | BSD-3-Clause **wrapper**, with linked FFmpeg libraries requiring separate review |
| FFmpeg | Build-dependent LGPL/GPL/nonfree. `LocalWhisper` checks `av._core.library_meta`, rejects unknown licenses and `--enable-gpl`/`--enable-nonfree`, and only accepts LGPL-labeled builds. |
| sipling v0.2 | Local MIT Python/source review and loopback integration completed. Streaming uses a separately compiled pjproject GPL/commercial helper, never a bundled binary. No pjsua2 runtime extra. Redistribution of a linked helper requires its own license compliance review; see `../sipling/docs/design.md`. |
| pytest / ruff / mypy / build / hatchling | Development/build tooling, not vendored runtime. MIT/Apache/BSD-family upstream licenses. |

Sources:

- [faster-whisper metadata](https://pypi.org/pypi/faster-whisper/1.2.1/json)
- [PyAV license](https://github.com/PyAV-Org/PyAV/blob/master/LICENSE.txt)
- [PyAV FFmpeg metadata extraction](https://github.com/PyAV-Org/PyAV/blob/master/av/_core.py)
- [PyAV FFmpeg build script](https://github.com/PyAV-Org/pyav-ffmpeg/blob/master/scripts/build-ffmpeg.py)
- [FFmpeg legal guidance](https://ffmpeg.org/legal.html)
- [tokenizers license](https://github.com/huggingface/tokenizers/blob/main/LICENSE)

The FFmpeg build path includes x264/x265 options: do not infer a binary's license
from PyAV's Python metadata. The runtime check is a defense against using an
incompatible build, **not a full transitive license audit**. Pip can install optional
binaries before the runtime guard runs. No STT binaries were installed or shipped
in this launch, and no claim is made that arbitrary future optional dependency
resolution is GPL-free. For redistribution, resolve and lock all dependencies,
inspect each wheel's licenses/SBOM and actual linked library metadata on every target
platform, provision an LGPL-only FFmpeg/PyAV build, retain its notices and meet LGPL
redistribution obligations. Review models independently. Pin the reviewed sipling
release and audit its entire transport stack before enabling the extra.


## Local v0.2 integration update

The optional constraint is now `sipling>=0.2,<0.3`; it describes compatibility,
not an available PyPI publication. Install the sibling checkout explicitly.
The Python wheel remains stdlib-only. A compiled `sipling-stream` links the user's
separate pjproject libraries and third-party codec libraries; the MIT source
license does not make that executable MIT-only. The locally tested build disables
sound, video, SSL and FFmpeg. No compiled helper or upstream sources were added
to either distribution. Production routing, optional STT binary audits and any
redistribution remain unverified; local integration does not clear those gates.
