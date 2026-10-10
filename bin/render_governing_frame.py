#!/usr/bin/env python3
"""Render docs/governing-frame.md from the ratified pin set.

Decision 29 (c38ea344) ratifies ``docs/governing-frame.md`` as the ``bootstrap``
input to the doctrine snapshot, and requires it to be generated rather than hand-written,
with CI checking the committed file is byte-identical to the rendered output.

Why a generator at all
-----------------------
The frame is the *table of contents* of the doctrine, not the doctrine. A census finding is
attributed to a frame by content-addressing this file, so the file must be:

1. **Deterministic** -- the same pins always render the same bytes, or the CI byte-identity
   check is noise.
2. **Stable across incidental edits** -- editing a pinned text must NOT mint a new frame.
   Only deliberate re-ratification may.
3. **Append-only in practice** -- a changed frame means a changed doctrine, and deliberately
   starts a new B1/B3 cohort rather than silently contaminating an existing one.

Those three properties are why the generator reads ``bin/governing-frame.pins.json`` and
**not** live git HEAD or live DB state. Reading HEAD would change the frame on every commit,
which is the fragmentation Decision 28 Ruling 3 rejected outright.

Usage
-----
    bin/render_governing_frame.py              # write docs/governing-frame.md
    bin/render_governing_frame.py --check      # exit 1 if committed file has drifted
    bin/render_governing_frame.py --stdout     # print, do not write
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PINS = ROOT / "bin" / "governing-frame.pins.json"
OUTPUT = ROOT / "docs" / "governing-frame.md"

# Bumping this is a format change, not a doctrine change. It is rendered into the file so a
# reader can tell which renderer produced it.
RENDERER_VERSION = 1

HEADER_NOTICE = """> **Generated file — do not edit by hand.**
> Rendered by `bin/render_governing_frame.py` from `bin/governing-frame.pins.json`.
> CI verifies this file is byte-identical to the rendered output, so a hand edit fails the
> build. To change the frame, change the pins — which is a doctrine change, reviewed like
> any other, and deliberately re-mints the bootstrap hash.
"""


def _label(path: Path) -> str:
    """Repo-relative label, but never crash if the path is outside the repo."""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def render(pins: dict) -> str:
    """Render the frame deterministically. Same pins in, same bytes out."""
    lines: list[str] = []
    lines.append("# Governing frame")
    lines.append("")
    lines.append(HEADER_NOTICE.rstrip())
    lines.append("")
    lines.append(
        "This is the **ratified governing frame**: the rule-set in force for a governed walk, "
        "named and pinned by revision. It is a table of contents, not the doctrine itself — the "
        "texts below are governed in their own repositories."
    )
    lines.append("")
    lines.append(
        "It is the `bootstrap` input to the content-addressed doctrine snapshot "
        "(`python/peb-kernel/src/peb_kernel/doctrine.py`), so every execution is attributed to "
        "the frame that governed it rather than to whatever happened to be loaded at the time."
    )
    lines.append("")

    lines.append("## Frame identity")
    lines.append("")
    lines.append(f"- Frame schema version: `{pins['frame_schema_version']}`")
    lines.append(f"- Renderer version: `{RENDERER_VERSION}`")
    lines.append("- Ratified by:")
    for decision in pins.get("ratified_by", []):
        lines.append(f"  - `{decision}`")
    lines.append("")

    lines.append("## Pinned rule-set in force")
    lines.append("")
    for pin in pins["pins"]:
        target = pin.get("path") or pin.get("locator")
        lines.append(f"### `{pin['id']}`")
        lines.append("")
        lines.append(f"- Target: `{target}`")
        lines.append(f"- Kind: `{pin['kind']}`")
        lines.append(f"- Pinned revision: `{pin['revision']}` (`{pin['revision_kind']}`)")
        if pin.get("note"):
            lines.append(f"- Note: {pin['note']}")
        lines.append("")

    lines.append("## Change discipline")
    lines.append("")
    lines.append(
        "Any change to the set of pinned texts, to their revisions, or to the ratifying "
        "decisions above is a **doctrine change**. It is reviewed as one, and it re-mints the "
        "bootstrap hash — deliberately, so that B1/B3 cohort queries segment by frame instead "
        "of silently mixing a governed run with an ungoverned one."
    )
    lines.append("")
    lines.append(
        "Edits to the pinned texts themselves do **not** move the frame unless the pins are "
        "deliberately updated. That separation is the point: a routine fix to a doctrine file "
        "must not retroactively re-frame executions that ran under the previous ratification."
    )
    lines.append("")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if the committed file has drifted")
    parser.add_argument("--stdout", action="store_true", help="print instead of writing")
    args = parser.parse_args()

    pins = json.loads(PINS.read_text(encoding="utf-8"))
    rendered = render(pins)

    if args.stdout:
        sys.stdout.write(rendered)
        return 0

    if args.check:
        if not OUTPUT.exists():
            print(f"FAIL: {_label(OUTPUT)} does not exist; run bin/render_governing_frame.py")
            return 1
        current = OUTPUT.read_text(encoding="utf-8")
        if current != rendered:
            print(
                f"FAIL: {_label(OUTPUT)} has drifted from the pins.\n"
                "  The governing frame is generated. Either the pins changed deliberately, or "
                "someone hand-edited a generated file.\n"
                "  Run: bin/render_governing_frame.py"
            )
            return 1
        print(f"OK: {_label(OUTPUT)} is byte-identical to the rendered output")
        return 0

    OUTPUT.write_text(rendered, encoding="utf-8")
    print(f"wrote {_label(OUTPUT)} ({len(rendered)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
