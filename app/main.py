"""Haven entrypoint."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from app import __version__
from app.api.proxy import router
from app.core.config import Settings, get_settings
from app.core.sanitizer import Sanitiser
from app.database.audit import AuditLog

# httpx logs every request URL at INFO. Bodies are never logged, but keep it
# quiet anyway so nothing about a request reaches the console by default.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


def create_app(
    settings: Settings | None = None,
    sanitiser: Sanitiser | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    """Build the app. Tests pass in a shared sanitiser and a fake upstream transport."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        cfg = settings or get_settings()
        app.state.settings = cfg
        app.state.sanitiser = sanitiser or Sanitiser(
            regions=cfg.regions,
            spacy_model=cfg.spacy_model,
            score_threshold=cfg.score_threshold,
            allow_list=cfg.allow_list,
        )
        app.state.audit = AuditLog(cfg.audit_db_path)
        await app.state.audit.open()
        async with httpx.AsyncClient(timeout=cfg.upstream_timeout, transport=transport) as http:
            app.state.http = http
            yield
        await app.state.audit.close()

    app = FastAPI(title="Haven", version=__version__, lifespan=lifespan)
    app.include_router(router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    return app


app = create_app()
