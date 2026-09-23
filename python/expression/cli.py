"""CLI for Expression metadata stream projection."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .metadata_stream import (
    project_metadata_stream,
    export_metadata_stream,
    validate_metadata_stream,
)
from .pipeline import extract_explicit_observations


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Expression metadata stream projection CLI"
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
        "--extractor-revision",
        default="expression-metadata-v0.1",
        help="Extractor revision identifier",
    )
    parser.add_argument(
        "--stream-id",
        help="Custom stream ID (generated if not provided)",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Validate the output stream before emitting it (non-zero exit on errors)",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON output",
    )

    args = parser.parse_args()

    # Load transcript
    with args.input.open() as f:
        transcript = json.load(f)

    # Project metadata stream
    stream = project_metadata_stream(
        transcript,
        extractor_revision=args.extractor_revision,
        stream_id=args.stream_id,
    )

    # Validate if requested
    if args.validate:
        errors = validate_metadata_stream(stream)
        if errors:
            print("Validation errors:", file=sys.stderr)
            for err in errors:
                print(f"  - {err}", file=sys.stderr)
            return 1

    # Export
    output = export_metadata_stream(stream)

    if args.output:
        with args.output.open("w") as f:
            json.dump(output, f, indent=2 if args.pretty else None)
    else:
        json.dump(output, sys.stdout, indent=2 if args.pretty else None)
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
