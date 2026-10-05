"""Application settings, loaded from environment variables (and an optional .env file)."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, computed_field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Values shipped in .env.example. The app refuses to start in production with these.
INSECURE_PLACEHOLDERS = {"", "change-me", "changeme", "admin", "password", "secret"}


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
    llm_chat_model: str = "openai/gpt-5-mini"
    llm_embedding_model: str = "openai/text-embedding-3-large"
    embedding_dims: int | None = 1024
    # Comma-separated model names offered in the Settings page (in addition to what the gateway lists)
    llm_model_options: str = "openai/gpt-5-mini,openai/gpt-5,openai/gpt-4.1,openai/gpt-4o"
    # Comma-separated "model:max_dims" pairs
    embedding_model_options: str = (
        "openai/text-embedding-3-large:3072,openai/text-embedding-3-small:1536"
    )
    llm_temperature: float | None = None
    llm_max_retries: int = 2
    # Instructor mode for structured output: tools (most portable via LiteLLM), json, json_schema, md_json
    instructor_mode: str = "tools"
    llm_timeout_seconds: float = 120.0
    embedding_max_retries: int = 3
    query_embedding_cache_ttl: int = 7 * 24 * 3600

    # --- Workflows / auto-mapping ---
    automap_pipeline: str = "automap"
    automap_concurrency: int = 4
    workflow_plugins: str = ""
    workflow_definitions_dir: str = ""

    # --- Vector search (pgvectorscale) ---
    diskann_query_rescore: int = 100
    diskann_query_search_list_size: int = 200
    diskann_rebuild_threshold: int = 10_000

    # --- Files ---
    vocabulary_dir: str = "/data/vocabulary"
    upload_dir: str = "/data/uploads"

    # --- Auth ---
    jwt_secret: str = "change-me"
    jwt_expire_minutes: int = 720
    cookie_name: str = "omop_session"
    cookie_secure: bool = False
    admin_username: str = "admin"
    admin_password: str = "change-me"
    login_max_failures: int = 5
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

    @model_validator(mode="after")
    def _check_secrets(self) -> "Settings":
        if self.app_env == "prod":
            weak = [
                name
                for name, value in (
                    ("JWT_SECRET", self.jwt_secret),
                    ("ADMIN_PASSWORD", self.admin_password),
                )
                if value.strip().lower() in INSECURE_PLACEHOLDERS
            ]
            if weak:
                raise ValueError(
                    f"Refusing to start with placeholder secrets: {', '.join(weak)}. "
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
