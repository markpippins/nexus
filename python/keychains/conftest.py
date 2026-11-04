"""Ensure ``keychains`` is importable when pytest runs from the repo root."""

import pathlib
import sys

_PACKAGE_PARENT = str(pathlib.Path(__file__).resolve().parents[1])
if _PACKAGE_PARENT not in sys.path:
    sys.path.insert(0, _PACKAGE_PARENT)
