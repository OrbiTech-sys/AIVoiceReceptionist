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


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    stop = asyncio.Event()
    tasks = []
    adapter = None
    if s.audiosocket_enabled:
        adapter = AudioSocketAdapter(s.audiosocket_host, s.audiosocket_port)
        telephony_base.register(adapter)
        await adapter.start()
    if s.run_workers:
        from .workers import run_forever

        tasks.append(asyncio.create_task(run_forever(stop)))
        # Load speech models in the background so the first call doesn't pay the start-up cost.
        from .voice import runtime

        asyncio.get_running_loop().run_in_executor(None, runtime.warmup)
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
        if request.url.path.startswith("/api/") and "cache-control" not in response.headers:
            response.headers["Cache-Control"] = "no-store"
        return response

    for r in (auth, business, catalog, appointments, records, notifications, receptionist, telephony, system):
        app.include_router(r.router)
    return app


app = create_app()
