"""``python -m app.models``: list, download, verify or pin the model service models."""
from __future__ import annotations

import argparse
import json
import logging
import sys

from app.models.downloads import CATALOG_PATH, ModelDownloadError, download, load_catalog, pin, select, verify


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.models", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="show the catalog")
    for name, text in (("download", "fetch and verify into the Hugging Face cache"),
                       ("verify", "check the cached copy without downloading")):
        command = commands.add_parser(name, help=text)
        command.add_argument("ids", nargs="*", help="model ids from the catalog")
        command.add_argument("--service", choices=("tts", "stt", "image"), help="every model of one service")
        command.add_argument("--cache-dir", help="Hugging Face hub cache (default: HF_HUB_CACHE)")
    pinning = commands.add_parser("pin", help="maintainers: record a repository commit and file digests")
    pinning.add_argument("id")
    pinning.add_argument("--repo", required=True)
    pinning.add_argument("--service", required=True, choices=("tts", "stt", "image"))
    pinning.add_argument("--revision", default="main")
    pinning.add_argument("--include", nargs="+", required=True, help="files the service reads")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        if args.command == "pin":
            catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
            catalog["models"].update(pin(args.id, args.repo, args.service, args.include, revision=args.revision))
            catalog["models"] = dict(sorted(catalog["models"].items()))
            CATALOG_PATH.write_text(json.dumps(catalog, indent=2) + "\n", encoding="utf-8")
            return 0
        catalog = load_catalog()
        if args.command == "list":
            for entry in catalog.values():
                sys.stdout.write(f"{entry.id}\t{entry.service}\t{entry.repo}@{entry.revision[:12]}\t{entry.size / 1e9:.2f} GB\n")
            return 0
        failed = False
        for entry in select(catalog, args.ids, args.service):
            if args.command == "download":
                sys.stdout.write(f"{entry.id}: {download(entry, cache_dir=args.cache_dir)}\n")
                continue
            problems = verify(entry, cache_dir=args.cache_dir)
            failed = failed or bool(problems)
            sys.stdout.write(f"{entry.id}: " + ("ok" if not problems else "; ".join(problems)) + "\n")
        return 1 if failed else 0
    except ModelDownloadError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
