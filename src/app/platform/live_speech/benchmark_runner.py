"""Offline benchmark runner for live speech runtime adapters."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .benchmark import benchmark_stt, benchmark_tts
from .stt_adapters import create_transcriber_from_env
from .tts_adapters import create_synthesizer_from_env


def sample_pcm_chunks(*, chunks: int = 3, samples_per_chunk: int = 3200) -> list[bytes]:
    sample = (2000).to_bytes(2, byteorder="little", signed=True)
    return [sample * samples_per_chunk for _ in range(chunks)]


def run_benchmarks() -> dict:
    stt_result = benchmark_stt(create_transcriber_from_env(), sample_pcm_chunks(), name="live-speech-stt")
    tts_result = benchmark_tts(create_synthesizer_from_env(), "hello from the live speech benchmark", name="live-speech-tts")
    return {
        "ok": True,
        "results": [stt_result.as_dict(), tts_result.as_dict()],
        "targets_ms": {
            "first_transcript_delta": 500,
            "first_audio_delta": 500,
            "cancel_audio_stop": 150,
        },
    }


def write_report(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run live speech adapter benchmarks.")
    parser.add_argument("--out", type=Path, default=Path("resources/data/live-speech-benchmarks/latest.json"))
    args = parser.parse_args()
    payload = run_benchmarks()
    write_report(args.out, payload)
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
