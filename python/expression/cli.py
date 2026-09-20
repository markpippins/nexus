"""Command-line review bundle generator for Expression."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .pipeline import build_expression_bundle


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a non-authoritative Expression review bundle")
    parser.add_argument("transcript", type=Path, help="Transcript JSON containing transcript_id and turns")
    parser.add_argument("--candidates", type=Path, help="Optional JSON alias catalog: {candidate_id: [alias, ...]}")
    parser.add_argument("--output", type=Path, help="Write bundle JSON here instead of stdout")
    args = parser.parse_args()

    transcript = json.loads(args.transcript.read_text(encoding="utf-8"))
    candidates = None
    if args.candidates:
        candidates = json.loads(args.candidates.read_text(encoding="utf-8"))
    bundle = build_expression_bundle(transcript, candidates=candidates)
    rendered = json.dumps(bundle, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
