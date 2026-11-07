from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    app_name: str = "AI Open-Source Project Investigator"

    github_token: str | None = None
    llm_api_key: str | None = None

    database_url: str | None = None
    redis_url: str | None = None

    vector_db_url: str | None = None
    vector_db_api_key: str | None = None
    # "qdrant" (local file store, default) or "pgvector" (vectors live in
    # PostgreSQL; used for hosting where the local disk is not persistent)
    vector_backend: str = "qdrant"
    
    groq_api_key: str | None = None
    query_analyzer_model: str = "openai/gpt-oss-120b"

    @field_validator("database_url")
    @classmethod
    def _use_psycopg_driver(cls, value: str | None) -> str | None:
        # Hosted Postgres providers hand out postgres:// or postgresql:// URLs,
        # which SQLAlchemy maps to psycopg2; this project ships psycopg 3.
        if value:
            for prefix in ("postgres://", "postgresql://"):
                if value.startswith(prefix):
                    return "postgresql+psycopg://" + value[len(prefix):]
        return value

    @field_validator("vector_backend")
    @classmethod
    def _check_vector_backend(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in ("qdrant", "pgvector"):
            raise ValueError("VECTOR_BACKEND must be 'qdrant' or 'pgvector'.")
        return value

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()