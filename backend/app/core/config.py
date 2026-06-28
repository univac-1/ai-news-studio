from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    NEWS_DATA_URL: str = (
        "https://storage.googleapis.com/ai-watch-feed-llm-watch-public/data/models.json"
    )
    BASIC_AUTH_USERNAME: str = "admin"
    BASIC_AUTH_PASSWORD: str = "change-me"
    APP_ENV: str = "development"
    FRONTEND_ORIGIN: str = "http://localhost:5173"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
