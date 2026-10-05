"""Runtime settings: env defaults with optional overrides stored in ``app_config``.

Each override also stores the env value it was made against (``<key>.__env_baseline``).
If the env default changes later (for example the provider was switched in .env), the
override is treated as stale and ignored, so switching providers never needs a DB edit.
"""

import re
from dataclasses import dataclass

from sqlmodel import Session, delete, select

from app.core import cache
from app.core.config import get_settings
from app.models import AppConfig

BASELINE_SUFFIX = ".__env_baseline"
CACHE_NS = "config"

# key -> (settings attribute, type)
OVERRIDABLE: dict[str, tuple[str, type]] = {
    "llm.model": ("llm_chat_model", str),
    "embedding.model": ("llm_embedding_model", str),
    "embedding.dims": ("embedding_dims", int),
}

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

    @property
    def version(self) -> str:
        return f"{self.llm_model}|{self.embedding_model}|{self.embedding_dims}"


def _env_value(key: str):
    attr, _ = OVERRIDABLE[key]
    return getattr(get_settings(), attr)


def _coerce(key: str, raw: str | None):
    if raw is None or raw == "":
        return None
    _, typ = OVERRIDABLE[key]
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
    return EffectiveConfig(
        llm_model=str(resolved["llm.model"]),
        embedding_model=embedding_model,
        embedding_dims=dims if dims is None else int(dims),  # type: ignore[arg-type]
        collection_name=collection_name(embedding_model, dims),  # type: ignore[arg-type]
        values=values,
    )


def update_config(session: Session, updates: dict[str, object]) -> EffectiveConfig:
    for key, value in updates.items():
        if key not in OVERRIDABLE:
            raise ValueError(f"Unknown setting: {key}")
        env_value = _env_value(key)
        if value is None or _stringify(value) == _stringify(env_value):
            # Setting a value back to the env default removes the override
            session.exec(delete(AppConfig).where(AppConfig.key.in_([key, key + BASELINE_SUFFIX])))  # type: ignore[attr-defined]
            continue
        _upsert(session, key, _stringify(value))
        _upsert(session, key + BASELINE_SUFFIX, _stringify(env_value))
    session.commit()
    cache.invalidate(CACHE_NS)
    return get_effective_config(session)


def reset_config(session: Session) -> EffectiveConfig:
    keys = [k for key in OVERRIDABLE for k in (key, key + BASELINE_SUFFIX)]
    session.exec(delete(AppConfig).where(AppConfig.key.in_(keys)))  # type: ignore[attr-defined]
    session.commit()
    cache.invalidate(CACHE_NS)
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


def collection_name(model: str, dims: int | None) -> str:
    """Name of the embedding space for a model + dimension pair, e.g. ``openai_text_embedding_3_large_1024``."""
    slug = re.sub(r"[^a-z0-9]+", "_", model.lower()).strip("_")
    return f"{slug}_{dims or 'native'}"
