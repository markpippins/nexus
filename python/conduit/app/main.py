"""
Kernel Runtime — FastAPI application entrypoint.

Wires together:
    - Delta ingestion API      (POST /delta)
    - State inspection API     (GET  /state)
    - Replay API               (GET  /replay)
    - Prometheus metrics       (GET  /metrics)
    - API key authentication   (middleware, opt-in)

Start:   uvicorn app.main:app --reload --port 3103
"""

import logging
import os
import sys
import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST

from app.models.error import (
    ErrorResponse,
    ErrorDetail,
    ERR_NOT_FOUND,
    ERR_VALIDATION,
    ERR_UNAUTHORIZED,
    ERR_INTERNAL,
    ERR_SERVICE_UNAVAILABLE,
)

from app.api.routes_delta import router as delta_router
from app.api.routes_state import router as state_router
from app.api.routes_replay import router as replay_router
from app.api.routes_admin import router as admin_router
from app.api.routes_sessions import router as sessions_router
from app.api.routes_breaker import router as breaker_router
from app.api.routes_receipts import router as receipts_router
from app.api.routes_consolidated import router as consolidated_router
from app.api.routes_lilac import (
    lilac_enabled_from_env,
    router as lilac_router,
)
from lilac_client import LilacClient

# ── Logging ──────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)

_log = logging.getLogger("kernel.api")

# ── Prometheus metrics ───────────────────────────────────────────────

REQUEST_COUNT = Counter(
    "kernel_requests_total",
    "Total HTTP requests by method, path, and status",
    ["method", "path", "status"],
)

REQUEST_DURATION = Histogram(
    "kernel_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "path"],
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)

# Gauges updated lazily on /metrics scrape — no per-request overhead
KERNEL_VERSION = Gauge("kernel_version", "Current kernel state version")
KERNEL_PLAN_COUNT = Gauge("kernel_plan_count", "Number of plans tracked")
KERNEL_RECEIPT_COUNT = Gauge("kernel_receipt_count", "Number of receipts stored")
KERNEL_IDENTITY_COUNT = Gauge("kernel_identity_count", "Number of resolved identities")
KERNEL_GRAPH_EDGE_COUNT = Gauge("kernel_graph_edge_count", "Number of graph edges")
KERNEL_LINEAGE_EVENT_COUNT = Gauge("kernel_lineage_event_count", "Number of lineage events")


def _update_state_gauges():
    """Refresh Prometheus gauges from current kernel state."""
    try:
        from app.services.reducer_service import current_state
        s = current_state()
        KERNEL_VERSION.set(s.get("version", 0))
        KERNEL_PLAN_COUNT.set(len(s.get("plans", [])))
        KERNEL_RECEIPT_COUNT.set(len(s.get("receipts", {})))
        KERNEL_IDENTITY_COUNT.set(len(s.get("identity_map", {})))
        KERNEL_GRAPH_EDGE_COUNT.set(len(s.get("graph_edges", [])))
        KERNEL_LINEAGE_EVENT_COUNT.set(len(s.get("lineage_events", [])))
    except Exception:
        pass  # engine not yet initialized


# ── API key auth ─────────────────────────────────────────────────────
#
# Layer (a): static keys via environment variable.
#
#   KERNEL_API_KEYS="sk-key-1,sk-key-2"    # multiple, comma-separated
#   KERNEL_API_KEY="sk-key-1"               # single-key shorthand (backward compat)
#
# If neither is set, auth is disabled (passthrough).
#
# Layer (b) — planned future (ask the architect):
#   - Key table in PostgreSQL with CRUD admin endpoints
#   - Per-key metadata (consumer label, last-used timestamp)
#   - Rotation without restart (re-read from DB on schedule or SIGHUP)
#   - Integration with Azure Key Vault / secret store
# ─────────────────────────────────────────────────────────────────────

_KEYS_CSV = os.environ.get("KERNEL_API_KEYS") or os.environ.get("KERNEL_API_KEY", "")
VALID_API_KEYS: set[str] = {k.strip() for k in _KEYS_CSV.split(",") if k.strip()} if _KEYS_CSV else set()
AUTH_ENABLED = bool(VALID_API_KEYS)

PUBLIC_PATHS = {
    "/",
    "/healthz",
    "/readyz",
    "/metrics",
    "/state/health",
    "/docs",
    "/redoc",
    "/openapi.json",
}


async def _check_auth(request: Request) -> Response | None:
    """Reject request if X-API-Key header is missing or not in VALID_API_KEYS set.

    Returns a 401 JSONResponse or None (allow).
    """
    if not AUTH_ENABLED:
        return None
    if request.url.path in PUBLIC_PATHS:
        return None
    # Allow swagger UI paths
    if request.url.path.startswith(("/docs/", "/redoc/", "/openapi.json")):
        return None
    key = request.headers.get("X-API-Key", "")
    if key not in VALID_API_KEYS:
        return JSONResponse(
            status_code=401,
            content=ErrorResponse(
                error=ErrorDetail(
                    code=ERR_UNAUTHORIZED,
                    message="Unauthorized: missing or invalid X-API-Key header",
                )
            ).model_dump(),
        )
    return None


# ── App ──────────────────────────────────────────────────────────────

app = FastAPI(
    title="WRP Kernel Runtime",
    version="0.1.0",
    description="Deterministic state machine for the Nexus WorkRequest Pipeline.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Metrics + auth middleware ───────────────────────────────────────


@app.middleware("http")
async def metrics_auth_middleware(request: Request, call_next):
    """Combined middleware: authenticate, then record metrics."""
    # Auth check — return 401 response immediately if auth fails
    auth_response = await _check_auth(request)
    if auth_response is not None:
        return auth_response

    # Metrics timing. `status` defaults to 500 because a raising endpoint never
    # yields a response — reading `response` here (the old shape) raised
    # UnboundLocalError inside the finally block, which replaced the endpoint's
    # own exception before the error handler could build the JSON envelope.
    start = time.monotonic()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        duration = time.monotonic() - start
        # Use matched route pattern for cleaner aggregation, fallback to path
        route_path = (
            request.scope.get("route")
            and getattr(request.scope["route"], "path", request.url.path)
        ) or request.url.path
        REQUEST_COUNT.labels(
            method=request.method,
            path=route_path,
            status=status,
        ).inc()
        REQUEST_DURATION.labels(
            method=request.method,
            path=route_path,
        ).observe(duration)


# ── Include routers ──────────────────────────────────────────────────

app.include_router(delta_router, prefix="/delta", tags=["delta"])
app.include_router(state_router, prefix="/state", tags=["state"])
app.include_router(replay_router, prefix="/replay", tags=["replay"])
app.include_router(admin_router, prefix="/admin", tags=["admin"])
app.include_router(sessions_router, prefix="/api/sessions", tags=["sessions"])
app.include_router(breaker_router, prefix="/api/breaker", tags=["breaker"])
app.include_router(receipts_router, prefix="/api/receipts", tags=["receipts"])
# Consolidated bare-/api surface (typespec/v1/conduit-kernel/python/operations.tsp).
# Legacy routers above stay mounted so rollback is checkout + restart. Static
# consolidated paths are registered before /api/{identifier} inside the module.
app.include_router(consolidated_router)


# ── Lilac proxy surface (plan 8261669) — STAGED-INERT ────────────────
#
# Mounted ONLY when CONDUIT_LILAC_URL is set. With the variable absent these
# paths do not exist at all, so the default write path is byte-identical to
# before this surface was added (AC6). The routes delegate to lilac-srv and
# hold no SQL of their own; a guard in tests/test_lilac_service_contract.py
# pins both properties.
if lilac_enabled_from_env():
    _lilac_client = LilacClient.from_env()
    if _lilac_client is not None:
        app.state.lilac_client = _lilac_client
        app.include_router(lilac_router, prefix="/lilac", tags=["lilac"])


# ── Exception handlers ──────────────────────────────────────────────
#
# Converts all errors to the standard envelope:
#   {"error": {"code": "...", "message": "...", "details": ...}}


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Pydantic validation errors → standard error envelope (422)."""
    return JSONResponse(
        status_code=422,
        content=ErrorResponse(
            error=ErrorDetail(
                code=ERR_VALIDATION,
                message="Request validation failed",
                details={"errors": exc.errors()},
            )
        ).model_dump(),
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """FastAPI HTTPException → standard error envelope.

    Catches all ``raise HTTPException(...)`` calls from route handlers.
    """
    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorResponse(
            error=ErrorDetail(
                code=_status_to_error_code(exc.status_code),
                message=exc.detail if isinstance(exc.detail, str) else str(exc.detail),
            )
        ).model_dump(),
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    """Unhandled exceptions → standard error envelope (500)."""
    _log.error("Unhandled exception: %s", exc, exc_info=True)
    return JSONResponse(
        status_code=500,
        content=ErrorResponse(
            error=ErrorDetail(code=ERR_INTERNAL, message="Internal server error")
        ).model_dump(),
    )


def _status_to_error_code(status: int) -> str:
    """Map HTTP status code to canonical error code."""
    if status == 401:
        return ERR_UNAUTHORIZED
    if status == 404:
        return ERR_NOT_FOUND
    if status == 422:
        return ERR_VALIDATION
    if status == 503:
        return ERR_SERVICE_UNAVAILABLE
    if status >= 500:
        return ERR_INTERNAL
    return f"HTTP_{status}"


# ── Metrics endpoint ────────────────────────────────────────────────


@app.get("/metrics", response_class=PlainTextResponse)
def metrics():
    """Prometheus metrics endpoint — served in text/plain format.

    Updates state gauges on each scrape so they reflect current
    kernel state without per-request overhead.
    """
    _update_state_gauges()
    return generate_latest()


# ── Liveness / Readiness probes ─────────────────────────────────────
#
# Kubernetes convention:
#   /healthz — liveness: is the process alive? (no deps)
#   /readyz  — readiness: can we accept traffic? (checks DB + engine)
#   /state/health — kept for backward compat (hybrid)


@app.get("/healthz")
def liveness():
    """Liveness probe — always returns 200 if the process is running."""
    # If this handler runs, the process is alive — no further checks needed.
    return {"status": "alive"}


@app.get("/readyz")
def readiness():
    """Readiness probe — checks DB connectivity and engine state.

    Returns 503 if the database is unreachable.
    """
    from sqlalchemy import text
    from app.models.db import engine as db_engine
    try:
        with db_engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ready", "kernel_version": _safe_get_version()}
    except Exception as exc:
        _log.warning("Readiness probe failed: %s", exc)
        return JSONResponse(
            status_code=503,
            content=ErrorResponse(
                error=ErrorDetail(
                    code=ERR_SERVICE_UNAVAILABLE,
                    message=f"Database unreachable: {exc}",
                )
            ).model_dump(),
        )


def _safe_get_version() -> int:
    """Return current kernel version, or 0 if engine not yet initialized."""
    try:
        from app.services.reducer_service import current_version
        return current_version()
    except RuntimeError:
        return 0


# ── Startup ─────────────────────────────────────────────────────────


@app.on_event("startup")
async def startup():
    """Ensure database tables exist on startup."""
    from app.models.db import Base, engine
    Base.metadata.create_all(bind=engine)
    _log.info("Kernel API started — tables verified in PG")
    if AUTH_ENABLED:
        _log.info("API key authentication enabled (%d key(s) loaded)", len(VALID_API_KEYS))
    else:
        _log.info("API key authentication disabled (set KERNEL_API_KEYS or KERNEL_API_KEY to enable)")


# ── Graceful shutdown ────────────────────────────────────────────────


@app.on_event("shutdown")
async def shutdown():
    """Flush in-memory engine state to PG on graceful shutdown.

    On SIGTERM (uvicorn worker stop, container stop), forces a snapshot
    of the current KernelState so the next restart has a recent checkpoint
    and reconstructs faster.

    This is an optimization, not a correctness fix — the delta log is
    the source of truth and already persisted before the engine reduces.
    Full reconstruction from PG is always possible without this hook.
    """
    try:
        from app.services.reducer_service import get_engine, snapshot_store, SNAPSHOT_EVERY
        engine = get_engine()
        version = engine.kernel_state.version
        if version > 0 and version % SNAPSHOT_EVERY != 0:
            snapshot_store.save(version, engine.kernel_state.to_dict())
            _log.info("Shutdown: forced snapshot at version=%d", version)
        elif version > 0:
            _log.info("Shutdown: snapshot already exists at version=%d", version)
        else:
            _log.info("Shutdown: engine idle (version=0), no snapshot needed")
    except RuntimeError:
        _log.info("Shutdown: engine not yet initialized — nothing to flush")
    except Exception:
        _log.warning("Shutdown: snapshot flush failed", exc_info=True)
    finally:
        # Close DB connections cleanly
        from app.models.db import engine as db_engine
        db_engine.dispose()
        _log.info("Kernel API stopped")


@app.get("/")
def root():
    return {
        "service": "WRP Kernel Runtime",
        "version": "0.1.0",
        "docs": "/docs",
    }
