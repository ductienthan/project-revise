from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from preflight.config import Settings
from preflight.core.errors import DomainError, NotFound
from preflight.db.base import utcnow
from preflight.db.session import make_engine, make_sessionmaker
from preflight.server.api.v1 import router as api_v1
from preflight.server.auth.ratelimit import RateLimiter
from preflight.server.deps import LoginRequired
from preflight.server.ui import pages

UI_DIR = Path(__file__).parent / "ui"
CSP = (
    "default-src 'self'; script-src 'self' https://unpkg.com https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'; form-action 'self'"
)


def create_app(settings: Settings | None = None, clock: Callable[[], datetime] = utcnow) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(title="Preflight", docs_url=None, redoc_url=None)
    app.state.settings = settings
    app.state.clock = clock
    app.state.engine = make_engine(settings.database_url)
    app.state.sessionmaker = make_sessionmaker(app.state.engine)
    app.state.rate_limiter = RateLimiter(settings.rate_limit_per_minute)

    app.include_router(api_v1)
    app.include_router(pages.router)
    app.mount("/static", StaticFiles(directory=UI_DIR / "static"), name="static")

    @app.exception_handler(NotFound)
    async def _not_found(request: Request, err: NotFound):
        return JSONResponse({"detail": str(err)}, status_code=404)

    @app.exception_handler(DomainError)
    async def _domain(request: Request, err: DomainError):
        return JSONResponse({"detail": str(err)}, status_code=409)

    @app.exception_handler(LoginRequired)
    async def _to_login(request: Request, _: LoginRequired):
        return RedirectResponse(f"/login?next={request.url.path}", status_code=303)

    @app.middleware("http")
    async def _security(request: Request, call_next):
        if settings.require_https:
            proto = request.headers.get("x-forwarded-proto", request.url.scheme)
            if proto != "https":
                if request.method == "GET":
                    return RedirectResponse(str(request.url.replace(scheme="https")), status_code=308)
                return JSONResponse({"detail": "HTTPS required"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = CSP
        if settings.require_https:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

    return app
