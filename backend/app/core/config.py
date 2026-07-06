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
    VOICEVOX_SPEAKER_ID_EXPERT: int = 2
    VOICEVOX_SPEED_SCALE: float = 1.1
    VOICEVOX_POST_PHONEME_LENGTH: float = 0.3
    GEMINI_PROJECT: str = ""
    GEMINI_LOCATION: str = "us-central1"
    NEWS_SEARCH_REFRESH_ENABLED: bool = True
    NEWS_SEARCH_MODEL: str = "gemini-2.5-flash"
    IMAGE_GEN_ENABLED: bool = True
    # サムネイルはクリック率に直結するため高品質な Nano Banana Pro、
    # スライド共通背景は使い回すので低コストな Nano Banana (flash-image) を使う
    IMAGE_GEN_SLIDE_MODEL: str = "gemini-2.5-flash-image"
    # 画像生成モデルは Vertex AI では global ロケーション提供のため、
    # テキスト用の GEMINI_LOCATION とは別に持つ
    IMAGE_GEN_LOCATION: str = "global"
    CHARACTER_OVERLAY_ENABLED: bool = True
    CHARACTER_OVERLAY_NAME: str = "zundamon"
    BGM_ENABLED: bool = True
    # 空なら backend/app/assets/bgm/ 配下の *.mp3/*.wav/*.m4a をソートして先頭を自動選択する
    BGM_FILE: str = ""
    BGM_VOLUME_DB: float = -22.0

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
