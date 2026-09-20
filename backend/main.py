"""SIH26187 Border Surveillance API — application entry point.

    C:\\pythonjarvis\\python.exe -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
"""
import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Receive, Scope, Send

from backend.core.api_config import api_settings
from backend.core.config import settings
from backend.core.dependencies import get_current_active_user
from backend.core.rate_limit import RateLimitMiddleware, limiter
from backend.core.runtime import runtime_state
from backend.database.database import check_db_connection, dispose_engine, enable_extensions
from backend.routers import alerts, auth, cameras, evidence, events, hardware, stats, zones
from backend.schemas.api import PingResponse, RootResponse
from backend.services.camera_monitor import camera_monitor_loop
from backend.services.evidence_service import evidence_service
from backend.services.analysis_jobs import analysis_jobs
from backend.services.notification import notification_service
from backend.services.system_metrics import system_health_recorder_loop
from backend.websocket import analysis_stream, stream
from backend.websocket.manager import manager

logging.basicConfig(
    level=getattr(logging, api_settings.LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)-8s pid=%(process)d [%(name)s] %(message)s",
)
logger = logging.getLogger("sih26187.main")

APP_TITLE = "SIH26187 Border Surveillance API"
APP_VERSION = "1.0.0"


async def _cancel(task: asyncio.Task | None) -> None:
    if task is None or task.done():
        return
    task.cancel()
    with suppress(asyncio.CancelledError, Exception):
        await task


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting %s v%s (environment=%s)", APP_TITLE, APP_VERSION, settings.ENVIRONMENT)
    app.state.camera_monitor_task = None
    app.state.health_recorder_task = None

    evidence_service.ensure_directories()
    logger.info("Evidence directories ready under %s", evidence_service.evidence_root)

    if await check_db_connection():
        await enable_extensions()
        logger.info("Database connection verified")
    else:
        # Start anyway so /health can report "down" and recover when PostgreSQL returns.
        logger.critical("Database unreachable at startup — API will report unhealthy until it recovers")

    if api_settings.CAMERA_MONITOR_ENABLED:
        app.state.camera_monitor_task = asyncio.create_task(camera_monitor_loop(), name="camera_monitor")
        app.state.health_recorder_task = asyncio.create_task(system_health_recorder_loop(), name="health_recorder")
        logger.info("Camera monitor and system health recorder started")
    else:
        logger.warning("Camera monitor disabled by configuration (CAMERA_MONITOR_ENABLED=False)")

    try:
        yield
    finally:
        logger.info("Shutting down %s", APP_TITLE)
        await _cancel(app.state.camera_monitor_task)
        await _cancel(app.state.health_recorder_task)
        await analysis_jobs.shutdown()
        closed = await manager.close_all()
        logger.info("Closed %d WebSocket connection(s)", closed)
        await dispose_engine()


app = FastAPI(
    title=APP_TITLE,
    version=APP_VERSION,
    description=(
        "Ministry of Home Affairs / SSB AI border surveillance backend. "
        "JWT authentication with role-based access control, full audit logging, "
        "real-time WebSocket streaming and SHA-256 evidence chain of custody."
    ),
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@app.exception_handler(IntegrityError)
async def integrity_error_handler(request: Request, exc: IntegrityError) -> JSONResponse:
    logger.warning("Integrity error on %s %s: %s", request.method, request.url.path, exc)
    return JSONResponse({"detail": "Request conflicts with existing data or a database constraint"},
                        status_code=status.HTTP_409_CONFLICT)


@app.exception_handler(SQLAlchemyError)
async def database_error_handler(request: Request, exc: SQLAlchemyError) -> JSONResponse:
    logger.exception("Database error on %s %s", request.method, request.url.path)
    return JSONResponse({"detail": "Database error"}, status_code=status.HTTP_503_SERVICE_UNAVAILABLE)


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    # Internals are logged, never returned to the client.
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse({"detail": "Internal server error"}, status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)


SECURITY_HEADERS = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
    "cache-control": "no-store",
}


class SecurityHeadersMiddleware:
    """Pure ASGI: adds headers to the single response start message and never touches the body stream
    (BaseHTTPMiddleware re-chunks and buffers every response, including large evidence downloads)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message) -> None:
            if message["type"] == "http.response.start":
                message.setdefault("headers", [])
                headers = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS.items():
                    headers.setdefault(name, value)
            await send(message)

        await self.app(scope, receive, send_with_headers)


class UploadSizeLimitMiddleware:
    """Reject oversized uploads from the Content-Length header before any of the body is read."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            declared = Headers(scope=scope).get("content-length")
            if declared and declared.isdigit() and int(declared) > api_settings.upload_max_bytes + 5 * 1024 * 1024:
                response = JSONResponse(
                    {"detail": f"Request body exceeds the {api_settings.UPLOAD_MAX_SIZE_MB} MB limit"},
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


# Order (outermost first): CORS -> security headers -> global rate limit -> upload size -> app.
# Rate-limited (429) and rejected (413) responses therefore still carry CORS and security headers.
app.add_middleware(UploadSizeLimitMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=api_settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Authenticated evidence files. Mounted before the routers so /evidence/files/... is served here
# while /evidence/{evidence_id} still reaches the evidence router.
from backend.services.evidence_files import secure_evidence_files  # noqa: E402

app.mount("/evidence/files", secure_evidence_files, name="evidence-files")

authenticated = [Depends(get_current_active_user)]
app.include_router(auth.router, prefix="/auth")
app.include_router(cameras.router, prefix="/cameras", dependencies=authenticated)
app.include_router(alerts.router, prefix="/alerts", dependencies=authenticated)
app.include_router(events.router, prefix="/events", dependencies=authenticated)
app.include_router(evidence.router, prefix="/evidence", dependencies=authenticated)
app.include_router(zones.router, prefix="/zones", dependencies=authenticated)
app.include_router(hardware.router, prefix="/hardware", dependencies=authenticated)
app.include_router(stats.router, prefix="/stats", dependencies=authenticated)
app.include_router(stats.public_router)
app.include_router(analysis_stream.router, prefix="/ws")
app.include_router(stream.router, prefix="/ws")


@app.get("/", response_model=RootResponse, tags=["System"], summary="System information")
async def root() -> RootResponse:
    return RootResponse(
        name=APP_TITLE,
        version=APP_VERSION,
        status="operational",
        environment=settings.ENVIRONMENT,
        docs_url="/docs",
        timestamp=datetime.now(timezone.utc),
    )


@app.get("/ping", response_model=PingResponse, tags=["System"], summary="Liveness and database check")
async def ping() -> PingResponse:
    db_ok = await check_db_connection()
    return PingResponse(
        status="ok" if db_ok else "degraded",
        db="connected" if db_ok else "disconnected",
        timestamp=datetime.now(timezone.utc),
    )


__all__ = ["app", "limiter", "manager", "notification_service", "runtime_state"]
