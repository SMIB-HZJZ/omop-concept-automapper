"""FastAPI application."""

import logging
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import (
    auth,
    concepts,
    config,
    embeddings,
    mapping,
    meta,
    source,
    tasks,
    users,
    vocabulary,
    workflows,
)
from app.core.config import get_settings
from app.core.logging import setup_logging
from app.vector.store import CollectionMissing
from app.workflows.plugins import load_plugins

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    setup_logging()
    load_plugins()
    logger.info("API started")
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="OMOP Concept Automapper API",
        version="2.0.0",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(CollectionMissing)
    async def _collection_missing(_: Request, exc: CollectionMissing):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    api = APIRouter(prefix="/api")
    for module in (
        meta,
        auth,
        users,
        config,
        vocabulary,
        source,
        mapping,
        concepts,
        embeddings,
        tasks,
        workflows,
    ):
        api.include_router(module.router)
    app.include_router(api)
    return app


app = create_app()
