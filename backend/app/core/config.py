"""Application settings, loaded from environment variables (and an optional .env file)."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, computed_field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Values shipped in .env.example or used as defaults. The app refuses to start in production with these.
INSECURE_PLACEHOLDERS = {
    "",
    "change-me",
    "changeme",
    "admin",
    "password",
    "secret",
    "postgres",
    "sk-litellm-change-me",
}
MIN_JWT_SECRET_LENGTH = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    # --- Runtime ---
    app_env: Literal["dev", "prod", "test"] = "prod"
    log_level: str = "INFO"
    log_format: Literal["json", "text"] = "json"
    cors_origins: str = "http://localhost:8501,http://localhost:5173"

    # --- PostgreSQL ---
    postgres_user: str = "postgres"
    postgres_password: str = "postgres"
    postgres_db: str = "auto_mapper"
    postgres_host: str = "postgres-db"
    postgres_port: int = 5432
    database_url_override: str | None = Field(default=None, alias="DATABASE_URL")

    # --- Redis / Celery ---
    redis_url: str = "redis://redis:6379/0"
    celery_broker_url: str | None = None
    celery_result_backend: str | None = None
    cache_ttl_seconds: int = 60

    # --- LLM gateway (LiteLLM proxy, OpenAI-compatible) ---
    llm_gateway_url: str = "http://litellm:4000"
    litellm_master_key: str = "sk-litellm-change-me"
    # Defaults chosen for mapping accuracy at mid-tier prices and European-language coverage
    # (see README, "Providers and default models"): Claude Sonnet 5.5 reranks/translates,
    # Gemini Embedding 2 retrieves.
    llm_chat_model: str = "anthropic/claude-sonnet-5-5"
    llm_embedding_model: str = "openai/text-embedding-3-large"
    embedding_dims: int | None = 1536
    # What is embedded per concept: its name, plus "context" (class and domain) and/or up to
    # 8 synonyms. Each choice is a separate embedding collection. The name alone retrieved best
    # in the 2026-11 evaluation (synonyms and context both lowered R@1).
    embedding_text: Literal["name", "name_context", "name_synonyms", "name_context_synonyms"] = (
        "name"
    )
    # Send query/document task types (Gemini task_type, Voyage/Cohere input_type; ignored for
    # providers without them)
    embedding_task_types: bool = True
    # Comma-separated model names offered in the Settings page (in addition to what the gateway lists)
    llm_model_options: str = (
        "anthropic/claude-sonnet-5-5,anthropic/claude-opus-5-5,anthropic/claude-fable-5-2,"
        "openai/gpt-6.1-terra,gemini/gemini-4-hydrogen,mistral/mistral-large-latest,"
        "openai/gpt-6-luna,ollama/mistral-small"
    )
    # Comma-separated "model:max_dims" pairs
    embedding_model_options: str = (
        "gemini/gemini-embedding-2:3072,voyage/voyage-4-large:2048,cohere/embed-v5.0-pro:1536,"
        "openai/text-embedding-3-large:3072,mistral/mistral-embed:1024,ollama/bge-m3:1024"
    )
    llm_temperature: float | None = 0.0
    # Model name fragments that reject sampling parameters: no temperature is sent to them
    # (Claude 5.x returns a 400 for a non-default temperature)
    llm_no_temperature_models: str = (
        "claude-fable-,claude-mythos-,claude-opus-5,claude-sonnet-5,claude-haiku-5"
    )
    # Reasoning effort for the chat model (reranker, translation, drug parser, agents), sent as
    # reasoning_effort; LiteLLM maps it to Claude's output_config.effort. Empty = the model's
    # default (Sonnet 5.5 and Fable 5.2: high, Opus 5.5: medium).
    llm_reasoning_effort: Literal["", "low", "medium", "high", "xhigh", "max"] = ""
    llm_max_retries: int = 2
    # Instructor mode for structured output: tools (most portable via LiteLLM), json, json_schema, md_json
    instructor_mode: str = "tools"
    # Per-model exceptions, comma-separated "<model name prefix>=<mode>"; the longest matching
    # prefix wins. Claude 5.5 via OpenRouter rejects the forced tool choice that "tools" sends.
    instructor_mode_overrides: str = "openrouter/anthropic/=json_schema"
    llm_timeout_seconds: float = 120.0
    embedding_max_retries: int = 8
    # Parallel embedding requests while embedding concepts, and texts per request (empty: a
    # per-provider default, see app.llm.embeddings.REQUEST_SIZES). Lower the concurrency for
    # providers with tight rate limits (e.g. a free tier).
    embedding_concurrency: int = 4
    embedding_request_size: int | None = None
    query_embedding_cache_ttl: int = 7 * 24 * 3600
    # Reranker answers are reused for an identical prompt and model (0 = never cache)
    llm_cache_ttl: int = 30 * 24 * 3600

    # --- Workflows / auto-mapping ---
    automap_pipeline: str = "automap"
    # Used instead of AUTOMAP_PIPELINE for drug vocabularies when no pipeline is chosen
    automap_drug_pipeline: str = "automap_drug"
    automap_concurrency: int = 4
    # Rerank cascade: comma-separated models that each pick a candidate (at least two). When
    # they all pick the same one it stands; otherwise the judge decides, seeing their picks.
    # Empty: one model (the configured LLM) reranks every term.
    rerank_voters: str = ""
    # Model that settles disagreements; empty = the configured LLM model
    rerank_judge: str = ""
    # Escalation: when the reranker's confidence is below RERANK_ESCALATE_BELOW (or it picks
    # nothing or fails), this model reranks the term again and its answer is used.
    # Empty or a threshold of 0 = no escalation.
    rerank_escalate_model: str = "anthropic/claude-opus-5-5"
    rerank_escalate_below: int = Field(default=8, ge=0, le=10)
    # Reasoning effort of the escalation model (empty = its default). Opus 5.5 at xhigh was the
    # best of the escalation options tested (Opus at medium/high, Fable 5.2, Sonnet at xhigh).
    rerank_escalate_effort: Literal["", "low", "medium", "high", "xhigh", "max"] = "xhigh"
    workflow_plugins: str = ""
    workflow_definitions_dir: str = ""

    # --- Vector search (pgvectorscale) ---
    # After a vocabulary import, re-embed concepts whose embedded text changed and embed new
    # concepts in domains the active collection already covers (costs embedding calls)
    auto_embed_after_vocabulary_import: bool = True
    # Query-time DiskANN settings. 400/400 finds 99.3% of the exact top 30 (200/100: 96%) at
    # ~20 ms per query (200/100: ~8 ms), negligible next to the LLM call (2026-11 evaluation)
    diskann_query_rescore: int = 400
    diskann_query_search_list_size: int = 400
    diskann_rebuild_threshold: int = 10_000
    # maintenance_work_mem for DiskANN index builds (more = faster builds of large collections)
    diskann_maintenance_work_mem: str = Field(default="1GB", pattern=r"^\d+\s*(kB|MB|GB|TB)?$")

    # --- Files ---
    vocabulary_dir: str = "/data/vocabulary"
    upload_dir: str = "/data/uploads"

    # --- Auth ---
    jwt_secret: str = "change-me"
    jwt_expire_minutes: int = 720
    cookie_name: str = "omop_session"
    # Secure (HTTPS-only) session cookie. Unset: on in prod, off in dev/test (http://localhost)
    cookie_secure: bool | None = None
    admin_username: str = "admin"
    admin_password: str = "change-me"
    login_max_failures: int = 5
    login_max_failures_per_user: int = 20
    login_lockout_seconds: int = 900

    @computed_field  # type: ignore[prop-decorator]
    @property
    def database_url(self) -> str:
        if self.database_url_override:
            return self.database_url_override
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def secure_cookies(self) -> bool:
        return self.cookie_secure if self.cookie_secure is not None else self.app_env == "prod"

    @property
    def broker_url(self) -> str:
        return self.celery_broker_url or _redis_db(self.redis_url, 1)

    @property
    def result_backend(self) -> str:
        return self.celery_result_backend or _redis_db(self.redis_url, 2)

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def llm_model_option_list(self) -> list[str]:
        return [m.strip() for m in self.llm_model_options.split(",") if m.strip()]

    @property
    def rerank_voter_list(self) -> list[str]:
        return [m.strip() for m in self.rerank_voters.split(",") if m.strip()]

    @property
    def embedding_model_option_map(self) -> dict[str, int | None]:
        result: dict[str, int | None] = {}
        for item in self.embedding_model_options.split(","):
            item = item.strip()
            if not item:
                continue
            name, _, dims = item.rpartition(":")
            if name and dims.isdigit():
                result[name] = int(dims)
            else:
                result[item] = None
        return result

    @property
    def workflow_plugin_list(self) -> list[str]:
        return [p.strip() for p in self.workflow_plugins.split(",") if p.strip()]

    def _db_password(self) -> str:
        if self.database_url_override:
            from sqlalchemy.engine import make_url

            return make_url(self.database_url_override).password or ""
        return self.postgres_password

    @field_validator("llm_temperature", "embedding_request_size", mode="before")
    @classmethod
    def _empty_temperature(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    @model_validator(mode="after")
    def _check_secrets(self) -> "Settings":
        if self.app_env == "prod":
            weak = [
                name
                for name, value in (
                    ("JWT_SECRET", self.jwt_secret),
                    ("ADMIN_PASSWORD", self.admin_password),
                    ("POSTGRES_PASSWORD", self._db_password()),
                    ("LITELLM_MASTER_KEY", self.litellm_master_key),
                )
                if value.strip().lower() in INSECURE_PLACEHOLDERS
            ]
            if len(self.jwt_secret.strip()) < MIN_JWT_SECRET_LENGTH and "JWT_SECRET" not in weak:
                weak.append(f"JWT_SECRET (shorter than {MIN_JWT_SECRET_LENGTH} characters)")
            if weak:
                raise ValueError(
                    f"Refusing to start with placeholder or weak secrets: {', '.join(weak)}. "
                    "Set them in .env (or APP_ENV=dev for local development)."
                )
        return self


def _redis_db(url: str, db: int) -> str:
    base, _, last = url.rpartition("/")
    if last.isdigit():
        return f"{base}/{db}"
    return f"{url.rstrip('/')}/{db}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
