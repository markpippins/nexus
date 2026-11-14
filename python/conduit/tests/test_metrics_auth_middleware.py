"""A handler exception must yield the 500 envelope, not an UnboundLocalError.

Regression guard for ``metrics_auth_middleware``: the function recorded metrics
in a ``finally`` block that read ``response``, so when the endpoint raised — the
path where ``call_next`` never returns — the middleware itself raised
``UnboundLocalError``. That replaced the real exception on its way to the
exception handler, so callers got a bare 500 instead of the documented
``{"error": {"code": ..., "message": ...}}`` envelope, and the failure was
logged as a middleware bug rather than the endpoint's.

The app is driven over ASGI directly (no httpx, which the hermetic CI job does
not install). Metrics are read back from the Prometheus registry so the test
also pins the status the middleware records for a failed request.

Usage:
  cd python/conduit
  PYTHONPATH=.:.. python3 -m pytest tests/test_metrics_auth_middleware.py -q
"""
import asyncio
import json
import os
import sys

import pytest

_CONDUIT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PYTHON_ROOT = os.path.dirname(_CONDUIT_ROOT)
for _p in (_CONDUIT_ROOT, _PYTHON_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from prometheus_client import REGISTRY  # noqa: E402

from app.main import ERR_INTERNAL, app  # noqa: E402

BOOM_PATH = "/__test_middleware_boom"


@app.get(BOOM_PATH)
def _boom():
    """Endpoint used only by this test file."""
    raise RuntimeError("endpoint exploded")


def _asgi_call(path: str, method: str = "GET"):
    """Drive the app over ASGI and return (status, body, headers, propagated).

    Starlette's ServerErrorMiddleware re-raises the endpoint exception after it
    has sent the 500, so the raised exception is returned rather than letting it
    abort the assertions: the point is which response reached the client and
    whether the middleware replaced the original error.
    """
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [],
        "scheme": "http",
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 45678),
    }
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    propagated = None
    try:
        asyncio.run(app(scope, receive, send))
    except Exception as exc:  # noqa: BLE001 - the propagated error is an assertion input
        propagated = exc

    start = next(m for m in sent if m["type"] == "http.response.start")
    body = b"".join(
        m.get("body", b"") for m in sent if m["type"] == "http.response.body"
    )
    headers = {k.decode().lower(): v.decode() for k, v in start.get("headers", [])}
    return start["status"], body, headers, propagated


class TestHandlerException:
    def test_handler_exception_returns_the_error_envelope(self):
        status, body, headers, propagated = _asgi_call(BOOM_PATH)

        assert not isinstance(propagated, UnboundLocalError), (
            "the middleware replaced the endpoint error with an UnboundLocalError"
        )
        assert isinstance(propagated, RuntimeError), (
            "the endpoint's own exception must propagate, not be replaced"
        )
        assert status == 500, f"expected 500, got {status}: {body[:200]!r}"
        assert headers.get("content-type", "").startswith("application/json"), (
            "a masked UnboundLocalError produces text/plain; the app's error "
            f"envelope is JSON (got {headers.get('content-type')!r})"
        )
        payload = json.loads(body)
        assert payload["error"]["code"] == ERR_INTERNAL
        assert payload["error"]["message"] == "Internal server error"

    def test_failed_request_is_counted_as_500(self):
        _asgi_call(BOOM_PATH)
        value = REGISTRY.get_sample_value(
            "kernel_requests_total",
            {"method": "GET", "path": BOOM_PATH, "status": "500"},
        )
        assert value is not None and value >= 1, (
            "the middleware must record the failed request as status=500"
        )

    def test_unhandled_middleware_error_is_not_masked(self):
        """The regression shape: no UnboundLocalError may escape the middleware."""
        status, body, _, propagated = _asgi_call(BOOM_PATH)

        if isinstance(propagated, UnboundLocalError):  # pragma: no cover - the bug
            pytest.fail(f"middleware raised UnboundLocalError instead of a 500: {propagated}")
        assert status == 500
        assert json.loads(body)["error"]["message"] == "Internal server error"
