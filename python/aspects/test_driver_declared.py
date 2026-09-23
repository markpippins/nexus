"""Driver-declaration regression tests for the aspects binding port.

Closes the gap found in engineer record 457a2eea: binding_port.py uses
asyncpg as its runtime driver, but no requirements file declared it — a
fresh environment passed the (fake-pool) test suite and then failed at
runtime with a baffling AttributeError on NoneType. These tests pin the
declaration and fail with an actionable message when the driver is
missing.

Self-contained: runnable via pytest AND directly (`python3 test_driver_declared.py`),
following the bin/tests/test_merge_pr.py convention. No database needed.
"""

from __future__ import annotations

import importlib
import pathlib
import sys

ASPECTS_DIR = pathlib.Path(__file__).resolve().parent
REQ_FILE = ASPECTS_DIR / "requirements.txt"


def _load_binding_port():
    if str(ASPECTS_DIR) not in sys.path:
        sys.path.insert(0, str(ASPECTS_DIR))
    return importlib.import_module("binding_port")


def test_requirements_declare_asyncpg():
    """requirements.txt exists and declares asyncpg >= 0.29."""
    assert REQ_FILE.is_file(), (
        f"missing {REQ_FILE}: the aspects binding port's runtime driver "
        "is not declared anywhere"
    )
    text = REQ_FILE.read_text()
    line = next(
        (l for l in text.splitlines()
         if l.strip() and not l.strip().startswith("#")
         and l.strip().lower().startswith("asyncpg")),
        None,
    )
    assert line is not None, "requirements.txt does not declare asyncpg"
    assert ">=" in line, f"asyncpg entry has no version constraint: {line!r}"
    minor = line.split(">=", 1)[1].strip()
    major, minor_num = minor.split(".")[:2]
    assert (int(major), int(minor_num)) >= (0, 29), (
        f"asyncpg pin below the proven minimum 0.29: {line!r}"
    )


def test_asyncpg_driver_importable():
    """The driver must be importable in the runtime environment.

    This is the test that FAILS when asyncpg is missing — the failure
    names the exact fix instead of a baffling AttributeError later.
    """
    try:
        import asyncpg  # noqa: F401
    except ImportError as exc:
        raise AssertionError(
            "asyncpg is not importable in this environment. The aspects "
            "binding port requires it at runtime. Fix: "
            "pip install -r python/aspects/requirements.txt"
        ) from exc


def test_binding_port_availability_flag_matches_reality():
    """binding_port's soft-import flag must agree with real importability."""
    binding_port = _load_binding_port()
    try:
        import asyncpg  # noqa: F401
        really_available = True
    except ImportError:
        really_available = False
    assert binding_port._ASYNCPG_AVAILABLE is really_available, (
        f"binding_port._ASYNCPG_AVAILABLE={binding_port._ASYNCPG_AVAILABLE} "
        f"but importability={really_available}"
    )


def test_create_binding_port_fails_fast_without_driver():
    """Hermetic: with the driver absent (injected None), the factory must
    raise an actionable RuntimeError naming the driver and the fix —
    never a bare AttributeError on NoneType."""
    binding_port = _load_binding_port()
    saved, saved_flag = binding_port.asyncpg, binding_port._ASYNCPG_AVAILABLE
    binding_port.asyncpg = None
    binding_port._ASYNCPG_AVAILABLE = False
    try:
        async def _call():
            await binding_port.create_binding_port("postgresql://x")

        _require_runtime_error(_call())
    finally:
        binding_port.asyncpg, binding_port._ASYNCPG_AVAILABLE = saved, saved_flag


def _require_runtime_error(coro):
    """Run the coroutine, expecting the fail-fast RuntimeError.

    Separate from the test body so the traceback stays clean under
    both pytest and the __main__ runner.
    """
    import asyncio

    try:
        asyncio.run(coro)
    except RuntimeError as exc:
        msg = str(exc)
        assert "asyncpg" in msg, f"guard message lacks the driver name: {msg!r}"
        assert "requirements.txt" in msg, (
            f"guard message lacks the fix path: {msg!r}"
        )
        return
    except AttributeError as exc:
        raise AssertionError(
            "unguarded factory surfaced AttributeError on missing driver"
        ) from exc
    raise AssertionError("create_binding_port did not fail without a driver")


if __name__ == "__main__":
    tests = [(k, v) for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failures = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  ok {name}")
        except AssertionError as exc:
            failures += 1
            print(f"  FAIL {name}: {exc}")
    print(f"{len(tests) - failures}/{len(tests)} passed")
    sys.exit(1 if failures else 0)
