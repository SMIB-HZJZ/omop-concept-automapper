"""Vector storage and search on Postgres with pgvector + pgvectorscale (StreamingDiskANN).

Each embedding space (model + dims) is a table ``emb_<collection>`` holding only
``concept_id``, the vector and the DiskANN filter labels (one smallint per OMOP domain).
Concept metadata and ATC7 codes are joined live at query time, so they never go stale.
"""

import hashlib
import logging
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol

import numpy as np
import psycopg
from pgvector.psycopg import register_vector
from sqlalchemy import text
from sqlmodel import Session, select

from app.core.config import get_settings
from app.models import EmbeddingCollection

logger = logging.getLogger(__name__)

# Concept classes the Streamlit app never embedded (boxed / marketed drug packs)
EXCLUDED_CLASS_FILTER = "LOWER(c.concept_class_id) NOT LIKE '%%box%%' AND LOWER(c.concept_class_id) NOT LIKE '%%marketed%%'"

CONCEPT_FIELDS = """
    c.concept_id, c.concept_name, c.concept_class_id, c.domain_id, c.vocabulary_id,
    c.concept_code, c.standard_concept, c.invalid_reason
"""


@dataclass
class SearchFilters:
    domains: Sequence[str] = ()
    vocabulary_id: str | None = None
    atc7_codes: Sequence[str] = ()


class VectorSearcher(Protocol):
    """What workflows need from a vector store (lets tests plug in fakes)."""

    def search(self, query: str, k: int, filters: SearchFilters) -> list[dict]: ...


def table_name_for(collection: str) -> str:
    name = "emb_" + re.sub(r"[^a-z0-9_]", "_", collection.lower())
    if len(name) > 55:
        name = name[:46] + "_" + hashlib.md5(collection.encode()).hexdigest()[:8]
    return name


def _ident(name: str) -> str:
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", name):
        raise ValueError(f"Unsafe identifier {name!r}")
    return name


def vector_literal(vector: Sequence[float]) -> str:
    return "[" + ",".join(f"{float(x):.8g}" for x in vector) + "]"


# --------------------------------------------------------------------------- registry


def get_collection(session: Session, name: str) -> EmbeddingCollection | None:
    return session.exec(select(EmbeddingCollection).where(EmbeddingCollection.name == name)).first()


def collection_exists(session: Session, name: str) -> bool:
    col = get_collection(session, name)
    if col is None:
        return False
    return bool(session.execute(text("SELECT to_regclass(:t)"), {"t": col.table_name}).scalar())


def ensure_collection(session: Session, name: str, model: str, dims: int) -> EmbeddingCollection:
    col = get_collection(session, name)
    if col is not None and col.dims != dims:
        raise ValueError(
            f"Collection {name} stores {col.dims}-dimensional vectors, but the model returned {dims}"
        )
    if col is None:
        col = EmbeddingCollection(
            name=name, model=model, dims=dims, table_name=table_name_for(name)
        )
        session.add(col)
    table = _ident(col.table_name)
    session.execute(
        text(
            f"""
            CREATE TABLE IF NOT EXISTS {table} (
                concept_id INTEGER PRIMARY KEY REFERENCES concept (concept_id) ON DELETE CASCADE,
                embedding vector({int(dims)}) NOT NULL,
                labels SMALLINT[] NOT NULL DEFAULT '{{}}',
                embedded_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
    )
    session.commit()
    session.refresh(col)
    return col


def drop_collection(session: Session, name: str) -> bool:
    col = get_collection(session, name)
    if col is None:
        return False
    session.execute(text(f"DROP TABLE IF EXISTS {_ident(col.table_name)}"))
    session.delete(col)
    session.commit()
    return True


def index_name(table: str) -> str:
    return _ident(f"{table}_diskann")


def create_index(conn: psycopg.Connection, table: str) -> None:
    settings = get_settings()
    with conn.cursor() as cur:
        cur.execute("SET maintenance_work_mem = '1GB'")
        cur.execute(
            f"CREATE INDEX IF NOT EXISTS {index_name(table)} ON {_ident(table)} "
            f"USING diskann (embedding vector_cosine_ops, labels)"
        )
        cur.execute(f"ANALYZE {_ident(table)}")
    conn.commit()
    logger.info("DiskANN index ready on %s (rescore=%s)", table, settings.diskann_query_rescore)


def drop_index(conn: psycopg.Connection, table: str) -> None:
    with conn.cursor() as cur:
        cur.execute(f"DROP INDEX IF EXISTS {index_name(table)}")
    conn.commit()


def has_index(session: Session, table: str) -> bool:
    return bool(session.execute(text("SELECT to_regclass(:i)"), {"i": index_name(table)}).scalar())


# --------------------------------------------------------------------------- domain labels


def ensure_domain_labels(conn: psycopg.Connection) -> None:
    """Give every OMOP domain a stable smallint label (new domains get the next free number)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO domain_label (domain_id, label)
            SELECT d.domain_id,
                   COALESCE((SELECT MAX(label) FROM domain_label), 0)
                     + ROW_NUMBER() OVER (ORDER BY d.domain_id)
            FROM (SELECT DISTINCT domain_id FROM concept WHERE standard_concept = 'S') d
            WHERE NOT EXISTS (SELECT 1 FROM domain_label dl WHERE dl.domain_id = d.domain_id)
            ON CONFLICT DO NOTHING
            """
        )
    conn.commit()


# --------------------------------------------------------------------------- writes


def bulk_upsert(
    conn: psycopg.Connection,
    table: str,
    rows: Iterable[tuple[int, Sequence[float], Sequence[int]]],
) -> int:
    """Load (concept_id, vector, labels) rows with binary COPY, replacing existing vectors."""
    register_vector(conn)
    table = _ident(table)
    count = 0
    with conn.cursor() as cur:
        cur.execute(
            f"CREATE TEMP TABLE IF NOT EXISTS _emb_stage (LIKE {table} INCLUDING DEFAULTS) ON COMMIT DELETE ROWS"
        )
        with cur.copy(
            "COPY _emb_stage (concept_id, embedding, labels) FROM STDIN WITH (FORMAT BINARY)"
        ) as copy:
            copy.set_types(["int4", "vector", "int2[]"])
            for concept_id, vector, labels in rows:
                copy.write_row([concept_id, np.asarray(vector, dtype=np.float32), list(labels)])
                count += 1
        cur.execute(
            f"""
            INSERT INTO {table} (concept_id, embedding, labels)
            SELECT s.concept_id, s.embedding, s.labels FROM _emb_stage s
            JOIN concept c ON c.concept_id = s.concept_id
            ON CONFLICT (concept_id) DO UPDATE
                SET embedding = EXCLUDED.embedding, labels = EXCLUDED.labels, embedded_at = now()
            """
        )
        inserted = cur.rowcount
    conn.commit()
    if inserted != count:
        logger.warning(
            "%d of %d vectors referenced unknown concepts and were skipped", count - inserted, count
        )
    return inserted


def prune_non_standard(conn: psycopg.Connection, table: str) -> int:
    with conn.cursor() as cur:
        cur.execute(
            f"""
            DELETE FROM {_ident(table)} e USING concept c
            WHERE c.concept_id = e.concept_id
              AND (c.standard_concept IS NULL OR c.standard_concept != 'S')
            """
        )
        removed = cur.rowcount
    conn.commit()
    return removed


def refresh_labels(conn: psycopg.Connection, table: str) -> int:
    ensure_domain_labels(conn)
    with conn.cursor() as cur:
        cur.execute(
            f"""
            UPDATE {_ident(table)} e SET labels = ARRAY[dl.label]::smallint[]
            FROM concept c JOIN domain_label dl ON dl.domain_id = c.domain_id
            WHERE c.concept_id = e.concept_id AND e.labels IS DISTINCT FROM ARRAY[dl.label]::smallint[]
            """
        )
        updated = cur.rowcount
    conn.commit()
    return updated


# --------------------------------------------------------------------------- reads


def embedding_status(session: Session, collection: str, domain: str | None = None) -> dict:
    col = get_collection(session, collection)
    exists = col is not None and collection_exists(session, collection)
    domain_sql = " AND c.domain_id = :domain" if domain else ""
    params = {"domain": domain} if domain else {}
    if exists:
        table = _ident(col.table_name)  # type: ignore[union-attr]
        row = session.execute(
            text(
                f"""
                SELECT COUNT(*) AS total, COUNT(e.concept_id) AS embedded
                FROM concept c LEFT JOIN {table} e ON e.concept_id = c.concept_id
                WHERE c.standard_concept = 'S' AND {EXCLUDED_CLASS_FILTER.replace("%%", "%")}{domain_sql}
                """
            ),
            params,
        ).one()
        points = session.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
        total, embedded = int(row.total), int(row.embedded)
    else:
        total = session.execute(
            text(
                f"SELECT COUNT(*) FROM concept c WHERE c.standard_concept = 'S' "
                f"AND {EXCLUDED_CLASS_FILTER.replace('%%', '%')}{domain_sql}"
            ),
            params,
        ).scalar_one()
        embedded, points = 0, 0
    return {
        "collection": collection,
        "exists": exists,
        "points_count": int(points),
        "total": int(total),
        "embedded": embedded,
        "pending": int(total) - embedded,
        "percentage": (embedded / total * 100) if total else 0.0,
    }


def search_vectors(
    session: Session,
    collection: EmbeddingCollection,
    query_vector: Sequence[float],
    k: int,
    filters: SearchFilters,
) -> list[dict]:
    settings = get_settings()
    table = _ident(collection.table_name)
    params: dict = {"q": vector_literal(query_vector), "k": k}
    where = ["c.standard_concept = 'S'"]
    if filters.vocabulary_id:
        where.append("c.vocabulary_id = :voc")
        params["voc"] = filters.vocabulary_id

    if filters.atc7_codes:
        # ATC7 narrows the space to a handful of drugs: filter first, then exact distances
        params["atc"] = list(filters.atc7_codes)
        if filters.domains:
            where.append("c.domain_id = ANY(:domains)")
            params["domains"] = list(filters.domains)
        sql = f"""
            WITH cand AS MATERIALIZED (
                SELECT concept_id, atc7_codes FROM concept_atc7 WHERE atc7_codes && CAST(:atc AS text[])
            )
            SELECT {CONCEPT_FIELDS}, cand.atc7_codes,
                   1 - (e.embedding <=> CAST(:q AS vector)) AS score
            FROM cand
            JOIN {table} e ON e.concept_id = cand.concept_id
            JOIN concept c ON c.concept_id = e.concept_id
            WHERE {" AND ".join(where)}
            ORDER BY e.embedding <=> CAST(:q AS vector)
            LIMIT :k
        """
    else:
        if filters.domains:
            labels = (
                session.execute(
                    text("SELECT label FROM domain_label WHERE domain_id = ANY(:d)"),
                    {"d": list(filters.domains)},
                )
                .scalars()
                .all()
            )
            if not labels:
                return []
            where.append("e.labels && CAST(:labels AS smallint[])")
            params["labels"] = list(labels)
        sql = f"""
            SELECT {CONCEPT_FIELDS}, ca.atc7_codes,
                   1 - (e.embedding <=> CAST(:q AS vector)) AS score
            FROM {table} e
            JOIN concept c ON c.concept_id = e.concept_id
            LEFT JOIN concept_atc7 ca ON ca.concept_id = e.concept_id
            WHERE {" AND ".join(where)}
            ORDER BY e.embedding <=> CAST(:q AS vector)
            LIMIT :k
        """
    session.execute(
        text(f"SET LOCAL diskann.query_rescore = {int(settings.diskann_query_rescore)}")
    )
    session.execute(
        text(
            f"SET LOCAL diskann.query_search_list_size = {int(settings.diskann_query_search_list_size)}"
        )
    )
    rows = session.execute(text(sql), params).mappings().all()
    session.rollback()  # end the transaction holding the SET LOCALs
    return [{**dict(r), "score": float(r["score"]), "rank": i + 1} for i, r in enumerate(rows)]


class PgVectorStore:
    """Text search over one embedding collection: embeds the query, then searches vectors."""

    def __init__(self, session: Session, collection_name: str, embedder):
        self.session = session
        self.collection_name = collection_name
        self.embedder = embedder

    def collection(self) -> EmbeddingCollection | None:
        col = get_collection(self.session, self.collection_name)
        if col is None or not collection_exists(self.session, self.collection_name):
            return None
        return col

    def search(self, query: str, k: int, filters: SearchFilters) -> list[dict]:
        col = self.collection()
        if col is None:
            raise CollectionMissing(self.collection_name)
        vector = self.embedder.embed_query(query)
        return search_vectors(self.session, col, vector, k, filters)


class CollectionMissing(Exception):
    def __init__(self, name: str):
        super().__init__(
            f"Embedding collection '{name}' does not exist yet. Embed standard concepts first "
            "(Import → Embeddings)."
        )
        self.name = name
