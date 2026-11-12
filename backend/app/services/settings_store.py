"""Runtime settings: env defaults with optional overrides stored in ``app_config``.

Each override also stores the env value it was made against (``<key>.__env_baseline``).
If the env default changes later (for example the provider was switched in .env), the
override is treated as stale and ignored, so switching providers never needs a DB edit.
"""

import re
from dataclasses import dataclass

from sqlmodel import Session, delete, select

from app.core.config import get_settings
from app.models import AppConfig

BASELINE_SUFFIX = ".__env_baseline"

# key -> (settings attribute, type)
OVERRIDABLE: dict[str, tuple[str, type]] = {
    "llm.model": ("llm_chat_model", str),
    "embedding.model": ("llm_embedding_model", str),
    "embedding.dims": ("embedding_dims", int),
    "embedding.text": ("embedding_text", str),
    "embedding.task_types": ("embedding_task_types", bool),
}
EMBEDDING_TEXT_OPTIONS = ["name", "name_context", "name_synonyms", "name_context_synonyms"]

DIMENSION_OPTIONS = [512, 1024, 1536, 2048, 3072]


@dataclass
class SettingValue:
    key: str
    value: str | int | None
    source: str  # env | override
    env_value: str | int | None
    stale_override: str | None = None


@dataclass
class EffectiveConfig:
    llm_model: str
    embedding_model: str
    embedding_dims: int | None
    collection_name: str
    values: list[SettingValue]
    embedding_text: str = "name"
    embedding_task_types: bool = False

    @property
    def version(self) -> str:
        return (
            f"{self.llm_model}|{self.embedding_model}|{self.embedding_dims}|"
            f"{self.embedding_text}|{self.embedding_task_types}"
        )


def _env_value(key: str):
    attr, _ = OVERRIDABLE[key]
    return getattr(get_settings(), attr)


def _coerce(key: str, raw: str | None):
    if raw is None or raw == "":
        return None
    _, typ = OVERRIDABLE[key]
    if typ is bool:
        return raw.strip().lower() in ("true", "1", "yes")
    return typ(raw)


def get_effective_config(session: Session) -> EffectiveConfig:
    rows = {row.key: row.value for row in session.exec(select(AppConfig)).all()}
    values: list[SettingValue] = []
    resolved: dict[str, object] = {}
    for key in OVERRIDABLE:
        env_value = _env_value(key)
        override = rows.get(key)
        baseline = rows.get(key + BASELINE_SUFFIX)
        if override is not None and baseline == _stringify(env_value):
            value = _coerce(key, override)
            values.append(SettingValue(key, value, "override", env_value))
        else:
            value = env_value
            values.append(SettingValue(key, value, "env", env_value, stale_override=override))
        resolved[key] = value

    embedding_model = str(resolved["embedding.model"])
    dims = resolved["embedding.dims"]
    text_mode = str(resolved["embedding.text"] or "name")
    if text_mode not in EMBEDDING_TEXT_OPTIONS:
        text_mode = "name"
    task_types = bool(resolved["embedding.task_types"])
    return EffectiveConfig(
        llm_model=str(resolved["llm.model"]),
        embedding_model=embedding_model,
        embedding_dims=dims if dims is None else int(dims),  # type: ignore[arg-type]
        collection_name=collection_name(embedding_model, dims, text_mode, task_types),  # type: ignore[arg-type]
        values=values,
        embedding_text=text_mode,
        embedding_task_types=task_types,
    )


def update_config(session: Session, updates: dict[str, object]) -> EffectiveConfig:
    for key, value in updates.items():
        if key not in OVERRIDABLE:
            raise ValueError(f"Unknown setting: {key}")
        env_value = _env_value(key)
        if key == "embedding.text" and value is not None and value not in EMBEDDING_TEXT_OPTIONS:
            raise ValueError(f"embedding_text must be one of {EMBEDDING_TEXT_OPTIONS}")
        if value is None or _stringify(value) == _stringify(env_value):
            # Setting a value back to the env default removes the override
            session.exec(delete(AppConfig).where(AppConfig.key.in_([key, key + BASELINE_SUFFIX])))  # type: ignore[attr-defined]
            continue
        _upsert(session, key, _stringify(value))
        _upsert(session, key + BASELINE_SUFFIX, _stringify(env_value))
    session.commit()
    return get_effective_config(session)


def reset_config(session: Session) -> EffectiveConfig:
    keys = [k for key in OVERRIDABLE for k in (key, key + BASELINE_SUFFIX)]
    session.exec(delete(AppConfig).where(AppConfig.key.in_(keys)))  # type: ignore[attr-defined]
    session.commit()
    return get_effective_config(session)


def get_raw(session: Session, key: str) -> str | None:
    row = session.get(AppConfig, key)
    return row.value if row else None


def _upsert(session: Session, key: str, value: str) -> None:
    row = session.get(AppConfig, key)
    if row is None:
        session.add(AppConfig(key=key, value=value))
    else:
        row.value = value
        session.add(row)


def _stringify(value: object) -> str:
    return "" if value is None else str(value)


def collection_name(
    model: str, dims: int | None, text_mode: str = "name", task_types: bool = False
) -> str:
    """Name of the embedding space, e.g. ``openai_text_embedding_3_large_1024``.

    Non-default embedding text and task types get a suffix (``…_1024_name_synonyms_tt``), so
    every variant is its own collection and can be compared side by side."""
    slug = re.sub(r"[^a-z0-9]+", "_", model.lower()).strip("_")
    name = f"{slug}_{dims or 'native'}"
    if text_mode and text_mode != "name":
        name += f"_{text_mode}"
    if task_types:
        name += "_tt"
    return name
