"""One-off copy of vectors from the old Qdrant collection into pgvector."""

import logging

from app.core import cache
from app.core.db import raw_connection, session_scope
from app.services.settings_store import get_effective_config
from app.vector import store
from app.worker.base import TrackedTask
from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True, base=TrackedTask, name="migration.qdrant_to_pgvector", lock_name="embed"
)
def qdrant_to_pgvector(
    self,
    qdrant_url: str = "http://qdrant:6333",
    collection: str = "omop_vocab",
    batch_size: int = 1000,
    user_id: int | None = None,
) -> dict:
    from qdrant_client import QdrantClient

    progress = self.progress
    progress.phase(f"Connecting to Qdrant at {qdrant_url}")
    client = QdrantClient(url=qdrant_url, timeout=300)
    info = client.get_collection(collection)
    vectors_config = info.config.params.vectors
    size = vectors_config.size if hasattr(vectors_config, "size") else None
    if size is None:
        raise ValueError("Named/multi-vector Qdrant collections are not supported")
    total = info.points_count or 0

    with session_scope() as session:
        cfg = get_effective_config(session)
        if cfg.embedding_dims and cfg.embedding_dims != size:
            raise ValueError(
                f"Qdrant collection '{collection}' has {size}-dimensional vectors but the current "
                f"setting is {cfg.embedding_dims}. Set the embedding model/dimensions that were used to "
                f"create it (Settings) and run the migration again."
            )
        col = store.ensure_collection(session, cfg.collection_name, cfg.embedding_model, size)

    copied = 0
    with raw_connection() as conn:
        store.drop_index(conn, col.table_name)
        progress.phase(f"Copying {total:,} vectors into {col.name}", total=total)
        offset = None
        try:
            while True:
                points, offset = client.scroll(
                    collection_name=collection,
                    limit=batch_size,
                    offset=offset,
                    with_vectors=True,
                    with_payload=False,
                )
                if points:
                    copied += store.bulk_upsert(
                        conn, col.table_name, ((int(p.id), p.vector, []) for p in points)
                    )
                progress.update(current=copied, message=f"Copied {copied:,}/{total:,} vectors")
                progress.check_cancelled()
                if offset is None:
                    break
            progress.phase("Removing non-standard concepts and assigning domain labels")
            removed = store.prune_non_standard(conn, col.table_name)
            store.refresh_labels(conn, col.table_name)
        finally:
            progress.phase("Building the DiskANN index")
            store.create_index(conn, col.table_name)
            cache.invalidate("embeddings")

    return {
        "collection": col.name,
        "source_collection": collection,
        "points_in_qdrant": total,
        "copied": copied,
        "removed_non_standard": removed,
    }
