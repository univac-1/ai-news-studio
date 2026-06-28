from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    NEWS_DATA_URL: str = (
        "https://storage.googleapis.com/ai-watch-feed-llm-watch-public/data/models.json"
    )
    BASIC_AUTH_USERNAME: str = "admin"
    BASIC_AUTH_PASSWORD: str = "change-me"
    APP_ENV: str = "development"
    FRONTEND_ORIGIN: str = "http://localhost:5173"
    STATIC_FILES_DIR: str = ""
    VOICEVOX_BASE_URL: str = "http://localhost:50021"
    VOICEVOX_SPEAKER_ID: int = 3
    GEMINI_PROJECT: str = ""
    GEMINI_LOCATION: str = "us-central1"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
