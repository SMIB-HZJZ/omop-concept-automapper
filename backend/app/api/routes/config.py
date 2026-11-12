import re

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import AdminUser, CurrentUser, SessionDep
from app.core.cache import cached
from app.core.config import get_settings
from app.llm.gateway import list_gateway_models
from app.schemas.api import (
    CollectionPreview,
    ConfigOut,
    ConfigUpdate,
    EmbeddingModelOption,
    ModelOptions,
)
from app.services import settings_store
from app.vector import store

router = APIRouter(prefix="/config", tags=["config"])


def _out(cfg: settings_store.EffectiveConfig) -> ConfigOut:
    settings = get_settings()
    return ConfigOut(
        llm_model=cfg.llm_model,
        embedding_model=cfg.embedding_model,
        embedding_dims=cfg.embedding_dims,
        embedding_text=cfg.embedding_text,
        embedding_task_types=cfg.embedding_task_types,
        collection_name=cfg.collection_name,
        values=[v.__dict__ for v in cfg.values],
        automap_pipeline=settings.automap_pipeline,
        gateway_url=settings.llm_gateway_url,
    )


@router.get("", response_model=ConfigOut)
def get_config(_: CurrentUser, session: SessionDep):
    return _out(settings_store.get_effective_config(session))


@router.put("", response_model=ConfigOut)
def update_config(body: ConfigUpdate, _: AdminUser, session: SessionDep):
    updates: dict = {}
    fields = body.model_fields_set
    if "llm_model" in fields:
        updates["llm.model"] = body.llm_model
    if "embedding_model" in fields:
        updates["embedding.model"] = body.embedding_model
    if "embedding_dims" in fields:
        updates["embedding.dims"] = body.embedding_dims
    if "embedding_text" in fields:
        updates["embedding.text"] = body.embedding_text
    if "embedding_task_types" in fields:
        updates["embedding.task_types"] = body.embedding_task_types
    try:
        return _out(settings_store.update_config(session, updates))
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.post("/reset", response_model=ConfigOut)
def reset_config(_: AdminUser, session: SessionDep):
    return _out(settings_store.reset_config(session))


# Gateway models that are not chat models (as in the Streamlit app, which kept only gpt*)
_NOT_CHAT = re.compile(
    r"(whisper|tts|dall-e|image|audio|realtime|transcribe|moderation|search|babbage|davinci"
    r"|computer-use|codex|embed|rerank|ft:|-instruct"
    r"|-\d{4}-\d{2}-\d{2}$|-\d{4}$)",  # dated snapshots: the undated alias is listed too
    re.IGNORECASE,
)


# Embedding models whose names do not contain "embed"
_EMBEDDING = re.compile(r"(embed|^voyage/|/voyage-|bge-|e5-|nomic|minilm)", re.IGNORECASE)


def classify_gateway_models(
    gateway: list[str], llm: list[str], embedding: dict[str, int | None]
) -> tuple[list[str], dict[str, int | None]]:
    """Merge gateway models into the configured options: configured ones first, the rest sorted."""
    extra_llm: list[str] = []
    for model in gateway:
        if "*" in model:
            continue  # wildcard routes are not concrete models
        if "rerank" in model.lower():
            continue
        if _EMBEDDING.search(model):
            embedding.setdefault(model, None)
        elif not _NOT_CHAT.search(model) and model not in llm:
            extra_llm.append(model)
    return llm + sorted(set(extra_llm)), embedding


@cached("models", ttl=3600)
def _model_options() -> dict:
    settings = get_settings()
    gateway = list_gateway_models()
    llm = list(settings.llm_model_option_list)
    embedding = dict(settings.embedding_model_option_map)
    if gateway:
        llm, embedding = classify_gateway_models(gateway, llm, embedding)
    if settings.llm_chat_model not in llm:
        llm.insert(0, settings.llm_chat_model)
    embedding.setdefault(settings.llm_embedding_model, None)
    return {
        "llm_models": llm,
        "embedding_models": [{"name": k, "max_dims": v} for k, v in embedding.items()],
        "dimension_options": settings_store.DIMENSION_OPTIONS,
        "gateway_models_available": gateway is not None,
    }


@router.get("/models", response_model=ModelOptions)
def model_options(_: CurrentUser):
    data = _model_options()
    return ModelOptions(
        llm_models=data["llm_models"],
        embedding_models=[EmbeddingModelOption(**m) for m in data["embedding_models"]],
        dimension_options=data["dimension_options"],
        gateway_models_available=data["gateway_models_available"],
    )


@router.get("/collection-preview", response_model=CollectionPreview)
def collection_preview(
    _: CurrentUser,
    session: SessionDep,
    model: str = Query(...),
    dims: int | None = Query(None),
    text: str = Query("name"),
    task_types: bool = Query(False),
):
    name = settings_store.collection_name(model, dims, text, task_types)
    exists = store.collection_exists(session, name)
    count = store.embedding_status(session, name)["embedded"] if exists else None
    return CollectionPreview(name=name, exists=exists, embedded_count=count)
