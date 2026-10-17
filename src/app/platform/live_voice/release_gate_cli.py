"""Command-line entry point for evaluating local live-call release evidence."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.platform.live_voice.release_gate import (
    LiveVoiceReleaseThresholds,
    evaluate_live_voice_log,
)
from app.platform.live_voice.diagnostics import LIVE_VOICE_STREAM_LOG_PATH


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate Omnix live conversation release evidence.")
    parser.add_argument("--log", type=Path, default=LIVE_VOICE_STREAM_LOG_PATH)
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--minimum-latency-samples", type=int, default=5)
    parser.add_argument("--minimum-quality-trials", type=int, default=10)
    parser.add_argument(
        "--scenario",
        action="append",
        dest="scenarios",
        help="Override the required scenario set; repeat once per scenario.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    defaults = LiveVoiceReleaseThresholds()
    thresholds = LiveVoiceReleaseThresholds(
        minimum_latency_samples=args.minimum_latency_samples,
        minimum_quality_trials=args.minimum_quality_trials,
        required_scenarios=tuple(args.scenarios) if args.scenarios else defaults.required_scenarios,
    )
    report = evaluate_live_voice_log(
        args.log,
        hours=args.hours,
        thresholds=thresholds,
    )
    print(json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True))
    if report.status == "pass":
        return 0
    if report.status == "insufficient":
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
