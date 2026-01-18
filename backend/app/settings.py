from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="")

    database_url: str = "sqlite:///./app.db"
    minio_endpoint: str = "http://localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "uploads"
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1"


settings = Settings()
