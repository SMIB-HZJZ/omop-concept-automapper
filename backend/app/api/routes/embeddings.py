from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import AdminUser, CurrentUser, SessionDep
from app.api.routes._tasks import start_task
from app.core import cache
from app.schemas.api import EmbeddingStatus, EmbedRequest, Message, TaskRef
from app.services import tasks as task_service
from app.services.settings_store import get_effective_config
from app.vector import store
from app.worker.tasks.embeddings import embed_standard_concepts, sync_atc7, sync_metadata

router = APIRouter(prefix="/embeddings", tags=["embeddings"])


@router.get("/status", response_model=EmbeddingStatus)
def embedding_status(
    _: CurrentUser,
    session: SessionDep,
    domain: list[str] | None = Query(None, description="Repeat for several domains"),
):
    cfg = get_effective_config(session)
    status_ = store.embedding_status(session, cfg.collection_name, tuple(sorted(domain or [])))
    col = store.get_collection(session, cfg.collection_name)
    return EmbeddingStatus(
        **status_,
        model=cfg.embedding_model,
        dims=col.dims if col else cfg.embedding_dims,
    )


@router.post("/embed", response_model=TaskRef, status_code=status.HTTP_202_ACCEPTED)
def embed(body: EmbedRequest, user: AdminUser, session: SessionDep):
    params = body.model_dump()
    if params["domain"] and not params["domains"]:
        params["domains"] = [params["domain"]]
    params.pop("domain")
    return start_task(session, "embeddings.embed", embed_standard_concepts, params, user)


@router.post("/sync-metadata", response_model=TaskRef, status_code=status.HTTP_202_ACCEPTED)
def start_sync_metadata(user: AdminUser, session: SessionDep):
    return start_task(session, "embeddings.sync_metadata", sync_metadata, {}, user)


@router.post("/sync-atc7", response_model=TaskRef, status_code=status.HTTP_202_ACCEPTED)
def start_sync_atc7(user: AdminUser, session: SessionDep):
    return start_task(session, "embeddings.sync_atc7", sync_atc7, {}, user)


@router.delete("", response_model=Message)
def clear_embeddings(
    _: AdminUser,
    session: SessionDep,
    confirm: str = Query(..., description="The current collection name, typed by the user"),
):
    cfg = get_effective_config(session)
    if confirm != cfg.collection_name:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Type the collection name ({cfg.collection_name}) to confirm",
        )
    running = task_service.running_in_group(session, "embed")
    if running:
        # DROP TABLE would wait on the running task's locks and stall every search behind it
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"A '{running}' task is running; cancel it first"
        )
    dropped = store.drop_collection(session, cfg.collection_name)
    cache.invalidate("embeddings")
    if not dropped:
        return Message(message=f"Collection {cfg.collection_name} had no embeddings")
    return Message(message=f"Deleted all embeddings in {cfg.collection_name}")
