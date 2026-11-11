"""FastAPI chat backend: wires routers, lifespan and static file serving."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import get_logger
from .db import close_mongo_connection, connect_to_mongo
from .routers import chats, files, push, users, websocket

logger = get_logger(__name__)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: connect to MongoDB. If this fails we log it clearly and re-raise
    # so the server doesn't come up in a half-broken state.
    try:
        logger.info("Startup: connecting to MongoDB...")
        await connect_to_mongo()
        logger.info("Startup: MongoDB connection established.")
    except Exception:
        logger.exception("Startup FAILED: could not connect to MongoDB.")
        raise

    yield

    # Shutdown: always try to close the connection, but never crash on the way out.
    try:
        logger.info("Shutdown: closing MongoDB connection...")
        await close_mongo_connection()
        logger.info("Shutdown: MongoDB connection closed.")
    except Exception:
        logger.exception("Shutdown error while closing MongoDB connection.")


tags_metadata = [
    {"name": "Auth & Users", "description": "Log in with a username and list users."},
    {"name": "Chats", "description": "Create direct messages / groups and fetch history."},
    {"name": "Files", "description": "Upload files and images to share in a chat."},
    {"name": "Notifications", "description": "Web Push subscriptions for notifications when the app is closed."},
    {"name": "WebSocket", "description": "Real-time messaging, typing indicators and presence (connect at `/ws?user_id=...`)."},
]

app = FastAPI(
    title="Chatting System",
    description=(
        "A Telegram-style chat backend built with **FastAPI**, **WebSockets** "
        "and **MongoDB**.\n\n"
        "Most endpoints require a `user_id` query parameter (simple username-based "
        "auth). Call `POST /api/login` first to obtain your `user_id`.\n\n"
        "The live chat UI is served at [`/`](/)."
    ),
    version="1.0.0",
    lifespan=lifespan,
    openapi_tags=tags_metadata,
    docs_url="/docs",
    redoc_url="/redoc",
)

# --------------------------------------------------------------------------- #
# Security headers
#
# These headers harden the app in the browser: they lock content loading to our
# own origin (blocking injected 3rd-party scripts / clickjacking) and explicitly
# switch OFF device features the chat never needs (camera, microphone, location,
# etc.), so the page cannot access user data or device hardware.
# --------------------------------------------------------------------------- #

# Swagger/ReDoc need a looser policy (they load from a CDN + inline scripts).
_DOCS_PATHS = ("/docs", "/redoc", "/openapi.json")

_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' https://fonts.googleapis.com; "
    "img-src 'self' data: blob:; "
    "font-src 'self' https://fonts.gstatic.com; "
    "connect-src 'self'; "          # covers same-origin fetch + ws/wss
    "media-src 'self' blob:; "
    "worker-src 'self'; "
    "manifest-src 'self'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'; "
    "object-src 'none'"
)

# Turn off device capabilities the app does not use.
_PERMISSIONS_POLICY = (
    "geolocation=(), microphone=(), camera=(), usb=(), payment=(), "
    "accelerometer=(), gyroscope=(), magnetometer=(), bluetooth=(), "
    "hid=(), serial=(), midi=(), display-capture=()"
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    headers = response.headers
    headers.setdefault("X-Content-Type-Options", "nosniff")
    headers.setdefault("X-Frame-Options", "DENY")
    headers.setdefault("Referrer-Policy", "no-referrer")
    headers.setdefault("Permissions-Policy", _PERMISSIONS_POLICY)
    headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    # Don't apply the strict CSP to the API docs (they'd fail to render).
    if not request.url.path.startswith(_DOCS_PATHS):
        headers.setdefault("Content-Security-Policy", _CSP)
    return response


# --------------------------------------------------------------------------- #
# Global exception handlers
#
# These catch anything the individual endpoints don't handle themselves, log a
# clear message describing which request failed and why, and return a clean JSON
# body so the error is easy to read in the browser's network/inspect response
# instead of crashing the app.
# --------------------------------------------------------------------------- #


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    # Expected, deliberate errors (401/403/404/...). Warn-level: not a crash.
    logger.warning(
        "HTTP %s on %s %s -> %s",
        exc.status_code,
        request.method,
        request.url.path,
        exc.detail,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": "http_error", "detail": exc.detail},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    logger.warning(
        "Validation error on %s %s: %s",
        request.method,
        request.url.path,
        exc.errors(),
    )
    return JSONResponse(
        status_code=422,
        content={"error": "validation_error", "detail": exc.errors()},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    # Anything unexpected: log the full traceback so the terminal shows exactly
    # what broke, and return a generic 500 so the client still gets a clean reply.
    logger.exception(
        "Unhandled error on %s %s: %s",
        request.method,
        request.url.path,
        exc,
    )
    return JSONResponse(
        status_code=500,
        content={
            "error": "internal_server_error",
            "detail": f"{type(exc).__name__}: {exc}",
        },
    )


app.include_router(users.router)
app.include_router(chats.router)
app.include_router(files.router)
app.include_router(push.router)
app.include_router(websocket.router)


# --------------------------------------------------------------------------- #
# Static frontend
#
# Uploaded files are no longer served from disk; they are stored in and streamed
# from MongoDB GridFS by the files router (GET /uploads/{file_id}).
# --------------------------------------------------------------------------- #


@app.get("/")
async def index():
    return FileResponse(str(FRONTEND_DIR / "index.html"))


app.mount("/", StaticFiles(directory=str(FRONTEND_DIR)), name="frontend")
