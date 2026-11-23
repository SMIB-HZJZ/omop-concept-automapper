import os
import uuid

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import StreamingResponse

from app.api.deps import AdminUser, CurrentUser, SessionDep
from app.api.routes._tasks import start_task
from app.core.config import get_settings
from app.models import SourceVocabulary
from app.schemas.api import (
    SOURCE_LANGUAGE_DESCRIPTION,
    VOCABULARY_DESCRIPTION_MAX,
    VOCABULARY_NAME_MAX,
    AuditEntry,
    AuditStat,
    CountResult,
    MappedPage,
    MappingStats,
    SourceVocabularyCreate,
    SourceVocabularyOut,
    SourceVocabularyUpdate,
    TaskRef,
    UnmappedPage,
    clean_language,
)
from app.services import mapping
from app.services.source_import import SourceImportError, read_header, validate_header
from app.worker.tasks.source import import_source_concepts

router = APIRouter(prefix="/source-vocabularies", tags=["source vocabularies"])

MAX_UPLOAD_BYTES = 500 * 1024 * 1024
_CHUNK = 1024 * 1024


def _save_upload(file: UploadFile, path: str) -> None:
    """Copy the upload to disk, stopping as soon as it exceeds MAX_UPLOAD_BYTES."""
    written = 0
    with open(path, "wb") as out:
        while chunk := file.file.read(_CHUNK):
            written += len(chunk)
            if written > MAX_UPLOAD_BYTES:
                raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "File is larger than 500 MB")
            out.write(chunk)


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
def create_vocabulary(body: SourceVocabularyCreate, _: AdminUser, session: SessionDep):
    if body.id is not None and session.get(SourceVocabulary, body.id):
        raise HTTPException(status.HTTP_409_CONFLICT, f"Source vocabulary {body.id} already exists")
    vocab = mapping.create_vocabulary(
        session,
        body.name,
        vocabulary_id=body.id,
        description=body.description,
        is_drug=body.is_drug,
        code_vocabularies=body.code_vocabularies,
        source_language=body.source_language,
    )
    return SourceVocabularyOut(**vocab.model_dump())


@router.patch("/{vocabulary_id}", response_model=SourceVocabularyOut)
def update_vocabulary(
    vocabulary_id: int, body: SourceVocabularyUpdate, _: AdminUser, session: SessionDep
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


_DESCRIPTION_FORM = Form(
    None,
    max_length=VOCABULARY_DESCRIPTION_MAX,
    description="What the terms denote, in 1-2 sentences (change or clear it later with PATCH)",
)

_CODES_FORM = Form(
    None,
    max_length=500,
    description=(
        "Comma-separated OMOP vocabulary_ids of the codes in source_value (e.g. ICD10), looked up "
        "and followed through Maps to before any AI step (change or clear it later with PATCH)"
    ),
)


_LANGUAGE_FORM = Form(
    None, max_length=8, description=SOURCE_LANGUAGE_DESCRIPTION + " (change it later with PATCH)"
)


def _parse_language(value: str | None) -> str | None:
    try:
        return clean_language(value)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


def _parse_codes(value: str | None) -> list[str] | None:
    """None when not sent (an empty form field also arrives as None); else the distinct ids."""
    if value is None:
        return None
    return list(dict.fromkeys(v.strip() for v in value.split(",") if v.strip()))


@router.post("/import", response_model=TaskRef, status_code=status.HTTP_202_ACCEPTED)
def import_new_vocabulary(
    user: AdminUser,
    session: SessionDep,
    file: UploadFile = File(...),
    name: str = Form(..., max_length=VOCABULARY_NAME_MAX),
    description: str | None = _DESCRIPTION_FORM,
    is_drug: bool = Form(False),
    code_vocabularies: str | None = _CODES_FORM,
    source_language: str | None = _LANGUAGE_FORM,
):
    """Import source concepts into a new vocabulary, which gets the next free id."""
    return _start_import(
        session,
        user,
        file,
        None,
        name,
        description,
        is_drug,
        _parse_codes(code_vocabularies),
        _parse_language(source_language),
    )


@router.post(
    "/{vocabulary_id}/concepts/import", response_model=TaskRef, status_code=status.HTTP_202_ACCEPTED
)
def import_concepts(
    vocabulary_id: int,
    user: AdminUser,
    session: SessionDep,
    file: UploadFile = File(...),
    name: str | None = Form(None, max_length=VOCABULARY_NAME_MAX),
    description: str | None = _DESCRIPTION_FORM,
    is_drug: bool | None = Form(None),
    code_vocabularies: str | None = _CODES_FORM,
    source_language: str | None = _LANGUAGE_FORM,
):
    """Add source concepts to a vocabulary (created with this id when it does not exist)."""
    if vocabulary_id < 1:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Vocabulary id must be a positive integer")
    return _start_import(
        session,
        user,
        file,
        vocabulary_id,
        name,
        description,
        is_drug,
        _parse_codes(code_vocabularies),
        _parse_language(source_language),
    )


def _start_import(
    session,
    user,
    file: UploadFile,
    vocabulary_id: int | None,
    name: str | None,
    description: str | None,
    is_drug: bool | None,
    code_vocabularies: list[str] | None = None,
    source_language: str | None = None,
):
    """``source_language``: None leaves an existing vocabulary's language as it is (an empty form
    field also arrives as None; clear it with PATCH)."""
    name = " ".join((name or "").split()) or None
    description = None if description is None else (" ".join(description.split()) or "")
    upload_dir = get_settings().upload_dir
    os.makedirs(upload_dir, exist_ok=True)
    path = os.path.join(upload_dir, f"{uuid.uuid4()}.csv")
    try:
        _save_upload(file, path)
        try:
            validate_header(read_header(path))
        except (SourceImportError, UnicodeDecodeError, StopIteration) as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

        vocab = session.get(SourceVocabulary, vocabulary_id) if vocabulary_id else None
        if vocab is None:
            if name is None:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "A new vocabulary needs a name")
            vocab = mapping.create_vocabulary(
                session,
                name,
                vocabulary_id=vocabulary_id,
                description=description,
                is_drug=bool(is_drug),
                code_vocabularies=code_vocabularies,
                source_language=source_language,
            )
        elif (
            name
            or is_drug is not None
            or description is not None
            or code_vocabularies is not None
            or source_language is not None
        ):
            if name:
                vocab.name = name
            if description is not None:
                vocab.description = description or None
            if is_drug is not None:
                vocab.is_drug = is_drug
            if code_vocabularies is not None:
                vocab.code_vocabularies = code_vocabularies
            if source_language is not None:
                vocab.source_language = source_language
            session.add(vocab)
            session.commit()
            mapping.invalidate_vocabulary(vocab.id)
        return start_task(
            session,
            "source.import",
            import_source_concepts,
            {"path": path, "vocabulary_id": vocab.id, "filename": file.filename},
            user,
        )
    except BaseException:
        # The worker owns (and deletes) the file only once the task has been queued
        if os.path.exists(path):
            os.remove(path)
        raise


@router.get("/{vocabulary_id}/unmapped", response_model=UnmappedPage)
def unmapped(
    vocabulary_id: int,
    _: CurrentUser,
    session: SessionDep,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=500),
    view: str = Query("todo", pattern="^(todo|suggested|flagged|all)$"),
    q: str | None = Query(None, max_length=200),
):
    _get_vocab(session, vocabulary_id)
    return mapping.get_unmapped(session, vocabulary_id, page, per_page, view, q)


@router.get("/{vocabulary_id}/mapped", response_model=MappedPage)
def mapped(
    vocabulary_id: int,
    _: AdminUser,
    session: SessionDep,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=500),
    q: str | None = Query(None, max_length=200),
):
    _get_vocab(session, vocabulary_id)
    return mapping.get_mapped_page(session, vocabulary_id, page, per_page, q)


@router.get("/{vocabulary_id}/mapped.csv", response_class=StreamingResponse)
def mapped_csv(vocabulary_id: int, _: AdminUser, session: SessionDep):
    _get_vocab(session, vocabulary_id)
    return StreamingResponse(
        mapping.iter_mapped_csv(vocabulary_id),
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
