import os
import shutil
import uuid

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import Response

from app.api.deps import AdminUser, CurrentUser, SessionDep
from app.api.routes._tasks import start_task
from app.core.config import get_settings
from app.models import SourceVocabulary
from app.schemas.api import (
    AuditEntry,
    AuditStat,
    CountResult,
    MappedRow,
    MappingStats,
    SourceVocabularyCreate,
    SourceVocabularyOut,
    SourceVocabularyUpdate,
    TaskRef,
    UnmappedPage,
)
from app.services import mapping
from app.services.source_import import SourceImportError, read_header, validate_header
from app.worker.tasks.source import import_source_concepts

router = APIRouter(prefix="/source-vocabularies", tags=["source vocabularies"])

MAX_UPLOAD_BYTES = 500 * 1024 * 1024


def _get_vocab(session, vocabulary_id: int) -> SourceVocabulary:
    vocab = session.get(SourceVocabulary, vocabulary_id)
    if vocab is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"Source vocabulary {vocabulary_id} not found"
        )
    return vocab


@router.get("", response_model=list[SourceVocabularyOut])
def list_vocabularies(_: CurrentUser, session: SessionDep):
    return mapping.list_source_vocabularies(session)


@router.post("", response_model=SourceVocabularyOut, status_code=status.HTTP_201_CREATED)
def create_vocabulary(body: SourceVocabularyCreate, _: CurrentUser, session: SessionDep):
    if session.get(SourceVocabulary, body.id):
        raise HTTPException(status.HTTP_409_CONFLICT, f"Source vocabulary {body.id} already exists")
    vocab = SourceVocabulary(
        id=body.id,
        name=body.name or f"Vocabulary {body.id}",
        description=body.description,
        is_drug=body.is_drug,
    )
    session.add(vocab)
    session.commit()
    session.refresh(vocab)
    mapping.invalidate_vocabulary(body.id)
    return SourceVocabularyOut(**vocab.model_dump())


@router.patch("/{vocabulary_id}", response_model=SourceVocabularyOut)
def update_vocabulary(
    vocabulary_id: int, body: SourceVocabularyUpdate, _: CurrentUser, session: SessionDep
):
    vocab = _get_vocab(session, vocabulary_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(vocab, field, value)
    session.add(vocab)
    session.commit()
    session.refresh(vocab)
    mapping.invalidate_vocabulary(vocabulary_id)
    match = [v for v in mapping.list_source_vocabularies(session) if v["id"] == vocabulary_id]
    return match[0] if match else SourceVocabularyOut(**vocab.model_dump())


@router.delete("/{vocabulary_id}", response_model=CountResult)
def delete_vocabulary(vocabulary_id: int, _: AdminUser, session: SessionDep):
    _get_vocab(session, vocabulary_id)
    return CountResult(count=mapping.delete_vocabulary(session, vocabulary_id))


@router.post(
    "/{vocabulary_id}/concepts/import", response_model=TaskRef, status_code=status.HTTP_202_ACCEPTED
)
def import_concepts(
    vocabulary_id: int,
    user: CurrentUser,
    session: SessionDep,
    file: UploadFile = File(...),
    name: str | None = Form(None),
    is_drug: bool | None = Form(None),
):
    if vocabulary_id < 1:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Vocabulary id must be a positive integer")
    upload_dir = get_settings().upload_dir
    os.makedirs(upload_dir, exist_ok=True)
    path = os.path.join(upload_dir, f"{uuid.uuid4()}.csv")
    with open(path, "wb") as out:
        shutil.copyfileobj(file.file, out, length=1024 * 1024)
    if os.path.getsize(path) > MAX_UPLOAD_BYTES:
        os.remove(path)
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "File is larger than 500 MB")
    try:
        validate_header(read_header(path))
    except (SourceImportError, UnicodeDecodeError, StopIteration) as exc:
        os.remove(path)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    vocab = mapping.get_or_create_vocabulary(session, vocabulary_id, name)
    if name or is_drug is not None:
        if name:
            vocab.name = name
        if is_drug is not None:
            vocab.is_drug = is_drug
        session.add(vocab)
        session.commit()
    return start_task(
        session,
        "source.import",
        import_source_concepts,
        {"path": path, "vocabulary_id": vocabulary_id, "filename": file.filename},
        user,
    )


@router.get("/{vocabulary_id}/unmapped", response_model=UnmappedPage)
def unmapped(
    vocabulary_id: int,
    _: CurrentUser,
    session: SessionDep,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=500),
):
    _get_vocab(session, vocabulary_id)
    return mapping.get_unmapped(session, vocabulary_id, page, per_page)


@router.get("/{vocabulary_id}/mapped", response_model=list[MappedRow])
def mapped(vocabulary_id: int, _: CurrentUser, session: SessionDep):
    _get_vocab(session, vocabulary_id)
    return mapping.get_mapped(session, vocabulary_id)


@router.get("/{vocabulary_id}/mapped.csv", response_class=Response)
def mapped_csv(vocabulary_id: int, _: CurrentUser, session: SessionDep):
    _get_vocab(session, vocabulary_id)
    return Response(
        content=mapping.mapped_csv(session, vocabulary_id),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="vocabulary_{vocabulary_id}_mappings.csv"'
        },
    )


@router.get("/{vocabulary_id}/stats", response_model=MappingStats)
def stats(vocabulary_id: int, _: CurrentUser, session: SessionDep):
    _get_vocab(session, vocabulary_id)
    return mapping.get_stats(session, vocabulary_id)


@router.get("/{vocabulary_id}/audit/stats", response_model=list[AuditStat])
def audit_stats(vocabulary_id: int, _: CurrentUser, session: SessionDep):
    _get_vocab(session, vocabulary_id)
    return mapping.get_audit_stats(session, vocabulary_id)


@router.get("/{vocabulary_id}/audit/recent", response_model=list[AuditEntry])
def audit_recent(
    vocabulary_id: int, _: CurrentUser, session: SessionDep, limit: int = Query(10, ge=1, le=500)
):
    _get_vocab(session, vocabulary_id)
    return mapping.get_audit_recent(session, vocabulary_id, limit)
