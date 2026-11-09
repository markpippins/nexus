"""Load the repository-level pytest hook inside python/nexus_core.

pytest pins rootdir to this package (python/nexus_core/pyproject.toml), and
conftest files above rootdir are never loaded, so the repo-root conftest.py
does not apply to the tests under here (including wrp/tests). This loads that
same implementation by path rather than keeping a second copy of the hook.

pytest collects hooks from the conftest module's attributes, so binding the
three hook functions here is equivalent to defining them. The collected skips
live on the pytest config rather than in module globals, so a session that
loads both this bridge and the root conftest still reports and fails once.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_ROOT_CONFTEST = Path(__file__).resolve().parents[2] / "conftest.py"

_spec = importlib.util.spec_from_file_location("nexus_root_conftest", _ROOT_CONFTEST)
_root = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_root)

pytest_sessionstart = _root.pytest_sessionstart
pytest_runtest_logreport = _root.pytest_runtest_logreport
pytest_sessionfinish = _root.pytest_sessionfinish
