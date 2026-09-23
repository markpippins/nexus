"""CLI for Expression: metadata stream projection (default) or review bundle (--bundle).

Two surfaces over the same transcript input:

- default (``--stream``): project a source-owned metadata stream
  (``expression.metadata_stream``), optionally validating before emitting;
- ``--bundle``: the original review-bundle generator
  (``expression.pipeline.build_expression_bundle``), restored so the
  documented pre-salvage invocation keeps working.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .metadata_stream import (
    export_metadata_stream,
    project_metadata_stream,
    validate_metadata_stream,
)
from .pipeline import build_expression_bundle


def _emit(payload: dict, output: Path | None, pretty: bool) -> None:
    if output:
        with output.open("w") as f:
            json.dump(payload, f, indent=2 if pretty else None)
    else:
        json.dump(payload, sys.stdout, indent=2 if pretty else None)
        print()


def _load_json(path: Path) -> dict:
    with path.open() as f:
        return json.load(f)


def _run_stream(args: argparse.Namespace) -> int:
    transcript = _load_json(args.input)
    stream = project_metadata_stream(
        transcript,
        extractor_revision=args.extractor_revision,
        stream_id=args.stream_id,
    )
    if args.validate:
        errors = validate_metadata_stream(stream)
        if errors:
            print("Validation errors:", file=sys.stderr)
            for err in errors:
                print(f"  - {err}", file=sys.stderr)
            return 1
    _emit(export_metadata_stream(stream), args.output, args.pretty)
    return 0


def _run_bundle(args: argparse.Namespace) -> int:
    transcript = _load_json(args.input)
    candidates = None
    if args.candidates:
        candidates = _load_json(args.candidates)
    bundle = build_expression_bundle(transcript, candidates=candidates)
    _emit(bundle, args.output, args.pretty)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Expression CLI: metadata stream projection (default) "
            "or review bundle (--bundle)"
        )
    )
    parser.add_argument(
        "input",
        type=Path,
        help="Input transcript JSON file",
    )
    parser.add_argument(
        "-o", "--output",
        type=Path,
        help="Output JSON file (default: stdout)",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON output",
    )

    surface = parser.add_mutually_exclusive_group()
    surface.add_argument(
        "--stream",
        action="store_true",
        help="Metadata stream projection (default surface; explicit form)",
    )
    surface.add_argument(
        "--bundle",
        action="store_true",
        help="Review bundle generation (legacy surface)",
    )

    stream_opts = parser.add_argument_group("metadata stream options (--stream)")
    stream_opts.add_argument(
        "--extractor-revision",
        default="expression-metadata-v0.1",
        help="Extractor revision identifier",
    )
    stream_opts.add_argument(
        "--stream-id",
        help="Custom stream ID (generated if not provided)",
    )
    stream_opts.add_argument(
        "--validate",
        action="store_true",
        help=(
            "Validate the output stream before emitting it "
            "(non-zero exit on errors)"
        ),
    )

    bundle_opts = parser.add_argument_group("review bundle options (--bundle)")
    bundle_opts.add_argument(
        "--candidates",
        type=Path,
        help="Optional JSON alias catalog: {candidate_id: [alias, ...]}",
    )

    args = parser.parse_args()

    if args.bundle:
        return _run_bundle(args)
    return _run_stream(args)


if __name__ == "__main__":
    sys.exit(main())
