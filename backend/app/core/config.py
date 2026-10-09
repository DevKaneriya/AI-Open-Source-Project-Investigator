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
    
    groq_api_key: str | None = None
    query_analyzer_model: str = "qwen/qwen3.8-27b"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()