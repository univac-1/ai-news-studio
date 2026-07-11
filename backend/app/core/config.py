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
    # セグメント導入イラストをVeo(image-to-video)で動くクリップにする。
    # 生成イラストを先頭フレームとして渡すため絵柄は変わらず、動きだけが加わる。
    # Veoは秒課金で高コスト(fast 1080pで$0.12/秒、8秒クリップ約$1)のため
    # fast系をデフォルトにし、クリップはキャッシュする。
    # 注意: preview系(veo-3.1-*-preview)はVertex AIのpredictで404になる
    # (モデル定義は見えるが実行アクセス不可)ため、GA版(-001)を使うこと。
    # veo-3.0系は2026-06-30廃止済み。
    VIDEO_GEN_ENABLED: bool = True
    VIDEO_GEN_MODEL: str = "veo-3.1-fast-generate-001"
    VIDEO_GEN_LOCATION: str = "us-central1"
    VIDEO_GEN_DURATION_SECONDS: int = 8
    CHARACTER_OVERLAY_ENABLED: bool = True
    CHARACTER_OVERLAY_NAME: str = "zundamon"
    BGM_ENABLED: bool = True
    # 空なら backend/app/assets/bgm/ 配下の *.mp3/*.wav/*.m4a をソートして先頭を自動選択する
    BGM_FILE: str = ""
    BGM_VOLUME_DB: float = -22.0
    SONG_ENABLED: bool = True
    # 歌唱ソング(a cappella)にドラム+ベースの伴奏をミックスするかどうか
    SONG_BACKING_ENABLED: bool = True
    # 伴奏の音量調整(dB)。負値でボーカルより控えめにする
    SONG_BACKING_GAIN_DB: float = -11.0
    # 以下はヒントに過ぎず、実行時は /singers を問い合わせて実在するスタイルを優先する
    VOICEVOX_SING_TEACHER_ID: int = 6000
    VOICEVOX_SING_SPEAKER_ID: int = 3061
    # 動画生成後にGeminiが完成パートを見た目チェックし、はみ出し等を自動リテイクする機能。
    # GEMINI_PROJECT未設定時は自動的にスキップされる(video_review.review_and_retake参照)。
    REVIEW_ENABLED: bool = True
    # Feature D: 週次完全自律運転(YouTube自動アップロード)向けのOAuth2設定。
    # 取得方法は docs/youtube-oauth.md と scripts/get_youtube_refresh_token.py を参照。
    YOUTUBE_CLIENT_ID: str = ""
    YOUTUBE_CLIENT_SECRET: str = ""
    YOUTUBE_REFRESH_TOKEN: str = ""
    YOUTUBE_UPLOAD_ENABLED: bool = False

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
