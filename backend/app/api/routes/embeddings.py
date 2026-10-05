from fastapi import APIRouter, Query, status

from app.api.deps import AdminUser, CurrentUser, SessionDep
from app.api.routes._tasks import start_task
from app.core import cache
from app.schemas.api import EmbeddingStatus, EmbedRequest, Message, QdrantMigrationRequest, TaskRef
from app.services.settings_store import get_effective_config
from app.vector import store
from app.worker.tasks.embeddings import embed_standard_concepts, sync_atc7, sync_metadata
from app.worker.tasks.migration import qdrant_to_pgvector

router = APIRouter(prefix="/embeddings", tags=["embeddings"])


@router.get("/status", response_model=EmbeddingStatus)
def embedding_status(_: CurrentUser, session: SessionDep, domain: str | None = Query(None)):
    cfg = get_effective_config(session)
    status_ = store.embedding_status(session, cfg.collection_name, domain)
    col = store.get_collection(session, cfg.collection_name)
    return EmbeddingStatus(
        **status_,
        model=cfg.embedding_model,
        dims=col.dims if col else cfg.embedding_dims,
    )


@router.post("/embed", response_model=TaskRef, status_code=status.HTTP_202_ACCEPTED)
def embed(body: EmbedRequest, user: AdminUser, session: SessionDep):
    return start_task(session, "embeddings.embed", embed_standard_concepts, body.model_dump(), user)


@router.post("/sync-metadata", response_model=TaskRef, status_code=status.HTTP_202_ACCEPTED)
def start_sync_metadata(user: AdminUser, session: SessionDep):
    return start_task(session, "embeddings.sync_metadata", sync_metadata, {}, user)


@router.post("/sync-atc7", response_model=TaskRef, status_code=status.HTTP_202_ACCEPTED)
def start_sync_atc7(user: AdminUser, session: SessionDep):
    return start_task(session, "embeddings.sync_atc7", sync_atc7, {}, user)


@router.delete("", response_model=Message)
def clear_embeddings(_: AdminUser, session: SessionDep):
    cfg = get_effective_config(session)
    dropped = store.drop_collection(session, cfg.collection_name)
    cache.invalidate("embeddings")
    if not dropped:
        return Message(message=f"Collection {cfg.collection_name} had no embeddings")
    return Message(message=f"Deleted all embeddings in {cfg.collection_name}")


@router.post("/migrate-from-qdrant", response_model=TaskRef, status_code=status.HTTP_202_ACCEPTED)
def migrate_from_qdrant(body: QdrantMigrationRequest, user: AdminUser, session: SessionDep):
    return start_task(
        session, "embeddings.migrate_qdrant", qdrant_to_pgvector, body.model_dump(), user
    )
