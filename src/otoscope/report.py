"""Dependency-free, escaped static reports, with relative local audio links."""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any


def write_report(directory: Path, results: list[dict[str, Any]]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "run.json").write_text(
        json.dumps({"schema_version": 1, "tests": results}, indent=2), encoding="utf-8"
    )
    escape = html.escape
    sections = []
    samples: dict[str, list[float]] = {
        "Turn gap": [],
        "Answer": [],
        "First audio": [],
        "Barge-in": [],
    }
    for result in results:
        metrics = result.get("metrics", {})
        samples["Turn gap"].extend(metrics.get("turn_gaps", []))
        samples["Barge-in"].extend(metrics.get("barge_in_latencies", []))
        for label, key in [("Answer", "answer_seconds"), ("First audio", "first_audio_seconds")]:
            if metrics.get(key) is not None:
                samples[label].append(metrics[key])
        ladder = " → ".join(f"{e['seconds']:.3f}s {e['name']}" for e in result.get("ladder", []))
        players = "".join(
            f'<label>{escape(name)}<audio controls preload="none" '
            f'src="{escape(result["artifact_id"] + "/" + name, quote=True)}">'
            "</audio></label>"
            for name in result.get("audio", [])
        )
        turns = "".join(
            f"<li>Turn {i}: {turn['duration']:.3f}s, WER "
            f"{turn['wer'] if turn['wer'] is not None else 'not scored'}</li>"
            for i, turn in enumerate(result.get("turns", []))
        )
        sections.append(
            f"<section><h2>{escape(result['test'])} — "
            f"{escape(result['outcome'])}</h2><p>{escape(ladder)}</p>"
            f"<pre>{escape(json.dumps(metrics, indent=2))}</pre><ul>{turns}</ul>"
            f"{players}</section>"
        )
    histograms = []
    for title, values in samples.items():
        buckets = [
            ("overlap", sum(v < 0 for v in values)),
            ("0–0.5s", sum(0 <= v < 0.5 for v in values)),
            ("0.5–1s", sum(0.5 <= v < 1 for v in values)),
            ("1–2.5s", sum(1 <= v < 2.5 for v in values)),
            ("≥2.5s", sum(v >= 2.5 for v in values)),
        ]
        bars = "".join(
            f'<div>{label}: <meter min="0" max="{max(1, len(values))}" '
            f'value="{count}"></meter> {count}</div>'
            for label, count in buckets
        )
        histograms.append(f"<h2>{title} latency histogram ({len(values)} samples)</h2>{bars}")
    document = (
        '<!doctype html><html lang="en"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width">'
        "<title>Otoscope regression report</title><style>"
        "body{font:16px system-ui;max-width:1000px;margin:2rem auto;padding:1rem}"
        "section{border-top:1px solid #aaa;padding:1rem 0}label{display:inline-grid;"
        "margin:0 1rem 1rem 0}pre{white-space:pre-wrap}meter{width:16rem}"
        "</style><h1>Otoscope regression report</h1><p>Energy-based observations; "
        "not perceptual quality scores. Audio may contain sensitive conversation.</p>"
        + "".join(histograms)
        + "".join(sections)
        + "</html>"
    )
    (directory / "index.html").write_text(document, encoding="utf-8")
