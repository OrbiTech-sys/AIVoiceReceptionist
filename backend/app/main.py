from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from .api import appointments, auth, business, catalog, notifications, receptionist, records, system, telephony
from .config import get_settings
from .errors import install_error_handlers
from .telephony import base as telephony_base
from .telephony.audiosocket import AudioSocketAdapter

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)


def _auto_install_models() -> None:
    """Runs in a worker thread: the check hashes any existing model files, which can take a few seconds."""
    from .voice import models_manager

    log = logging.getLogger("voicerep.models")
    try:
        started = models_manager.auto_install()
        if started:
            log.info("downloading speech models in the background: %s", ", ".join(started))
    except Exception:
        log.exception("automatic speech model install failed; install them from the Platform admin page")


def _recover_interrupted_calls() -> None:
    from .voice.session import recover_interrupted_calls

    log = logging.getLogger("voicerep.calls")
    try:
        closed = recover_interrupted_calls()
        if closed:
            log.warning("closed %d call(s) left in progress by the previous run", closed)
    except Exception:
        log.exception("could not close calls left in progress")


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    stop = asyncio.Event()
    tasks = []
    adapter = None
    if s.run_workers:
        # Before the phone listener opens, so this can only touch calls from the previous run.
        await asyncio.get_running_loop().run_in_executor(None, _recover_interrupted_calls)
    if s.audiosocket_enabled:
        adapter = AudioSocketAdapter(s.audiosocket_host, s.audiosocket_port)
        telephony_base.register(adapter)
        await adapter.start()
    if s.run_workers:
        from .workers import run_forever

        tasks.append(asyncio.create_task(run_forever(stop)))
        # Load speech models in the background so the first call doesn't pay the start-up cost.
        from .voice import runtime

        asyncio.get_running_loop().run_in_executor(None, runtime.startup_warmup)
        if s.auto_install_models:
            asyncio.get_running_loop().run_in_executor(None, _auto_install_models)
    yield
    stop.set()
    for t in tasks:
        t.cancel()
    if adapter:
        await adapter.stop()


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title="VoiceRep API", version="1.0.0", lifespan=lifespan,
                  docs_url="/api/docs" if s.env != "production" else None, openapi_url="/api/openapi.json")
    install_error_handlers(app)
    app.add_middleware(CORSMiddleware, allow_origins=s.origins, allow_credentials=True,
                       allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"], allow_headers=["content-type", "x-csrf-token"])

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        if get_settings().env == "production":
            response.headers.setdefault("Strict-Transport-Security", "max-age=63072000; includeSubDomains")
        if request.url.path.startswith("/api/"):
            if "cache-control" not in response.headers:
                response.headers["Cache-Control"] = "no-store"
            # API answers are data, never pages: if one were ever opened in a browser it may load nothing at all.
            if not request.url.path.startswith(("/api/docs", "/api/openapi")):
                response.headers.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
        return response

    for r in (auth, business, catalog, appointments, records, notifications, receptionist, telephony, system):
        app.include_router(r.router)
    return app


app = create_app()
