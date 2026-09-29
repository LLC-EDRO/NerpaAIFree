"""Command-line entrypoint for local parser inspection."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.presentation.parser.agent import PresentationParser
from app.presentation.parser.storage import FileSystemAssetStorage, NullAssetStorage


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Parse a PPTX into PresentationModel JSON")
    parser.add_argument("pptx", type=Path)
    parser.add_argument("--output", "-o", type=Path)
    parser.add_argument("--assets-dir", type=Path)
    parser.add_argument("--indent", type=int, default=2)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    storage = FileSystemAssetStorage(args.assets_dir) if args.assets_dir else NullAssetStorage()
    result = PresentationParser(asset_storage=storage).parse(args.pptx)
    rendered = result.model_dump_json(indent=args.indent)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        sys.stdout.write(rendered + "\n")
    return 0 if result.passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
