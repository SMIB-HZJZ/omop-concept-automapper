"""FastAPI application."""

import logging
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError

from app import __version__
from app.api.routes import (
    auth,
    collaboration,
    concepts,
    config,
    embeddings,
    mapping,
    meta,
    releases,
    source,
    tasks,
    users,
    vocabulary,
    workflows,
)
from app.core.config import get_settings
from app.core.db import DEADLOCK_SQLSTATE, LOCKED_SQLSTATE
from app.core.logging import setup_logging
from app.llm.embeddings import GatewayUnavailable
from app.vector.store import CollectionMissing
from app.workflows.plugins import load_plugins

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    setup_logging()
    load_plugins()
    logger.info("API started")
    yield


def _publish_task_result_schemas(app: FastAPI) -> None:
    """Add the task result models to the OpenAPI components so the frontend gets their types."""
    from fastapi.openapi.utils import get_openapi
    from pydantic.json_schema import models_json_schema

    from app.schemas.task_results import TASK_RESULT_MODELS

    def openapi() -> dict:
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
        _, defs = models_json_schema(
            [(m, "serialization") for m in TASK_RESULT_MODELS.values()],
            ref_template="#/components/schemas/{model}",
        )
        schema.setdefault("components", {}).setdefault("schemas", {}).update(defs["$defs"])
        app.openapi_schema = schema
        return schema

    app.openapi = openapi  # type: ignore[method-assign]


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="OMOP Concept Automapper API",
        version=__version__,
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

    @app.exception_handler(GatewayUnavailable)
    async def _gateway_unavailable(_: Request, exc: GatewayUnavailable):
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    @app.exception_handler(DBAPIError)
    async def _locked_vocabulary(_: Request, exc: DBAPIError):
        # The lock trigger of released vocabularies (migration 0016), and a deadlock (one of two
        # writers cancelled; trying again works); other errors stay 500s
        sqlstate = getattr(exc.orig, "sqlstate", None)
        if sqlstate == LOCKED_SQLSTATE:
            return JSONResponse(status_code=409, content={"detail": exc.orig.diag.message_primary})
        if sqlstate == DEADLOCK_SQLSTATE:
            return JSONResponse(
                status_code=409,
                content={
                    "detail": "Someone else changed the same data at the same moment; try again."
                },
            )
        raise exc

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
        collaboration,
        releases,
    ):
        api.include_router(module.router)
    app.include_router(api)
    _publish_task_result_schemas(app)
    return app


app = create_app()
