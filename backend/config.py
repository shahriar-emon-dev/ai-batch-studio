import os
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

class Settings(BaseSettings):
    # Supabase config
    supabase_url: str = Field(default="", validation_alias="SUPABASE_URL")
    supabase_anon_key: str = Field(default="", validation_alias="SUPABASE_KEY")
    supabase_service_role_key: str = Field(default="", validation_alias="SUPABASE_SERVICE_ROLE_KEY")
    supabase_jwt_secret: str = Field(default="", validation_alias="SUPABASE_JWT_SECRET")

    # Security
    encryption_key: str = Field(default="", validation_alias="ENCRYPTION_KEY")
    allowed_origins: str = Field(default="http://localhost:8000,http://127.0.0.1:8000,http://localhost:3000", validation_alias="ALLOWED_ORIGINS")

    # Paths. `OUTPUT_DIR` may be relative (resolved against the project root) or
    # absolute; the sub-directories are derived from it in __init__ so they can
    # never drift apart, and everything ends up absolute so the server behaves
    # the same regardless of the working directory it was started from.
    base_dir: str = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    output_dir: str = Field(default="output", validation_alias="OUTPUT_DIR")
    images_dir: str = ""
    audio_dir: str = ""
    videos_dir: str = ""
    merged_dir: str = ""
    uploads_dir: str = Field(default="uploads", validation_alias="UPLOADS_DIR")

    # API Defaults
    default_concurrency: int = Field(default=3, validation_alias="MAX_CONCURRENT_JOBS")

    # Model IDs (overridable per deployment without code changes)
    image_model: str = Field(default="gemini-2.5-flash-image", validation_alias="DEFAULT_IMAGE_MODEL")
    # Tried in order when the primary model is not available to the account.
    # Google retires model ids per project, so a single hardcoded id breaks
    # silently; the chain is walked only for "unavailable" errors, never for
    # quota (every model shares the same account quota).
    image_model_fallbacks: str = Field(
        default="gemini-3.1-flash-image,gemini-3-pro-image,gemini-2.5-flash-image-preview",
        validation_alias="DEFAULT_IMAGE_MODEL_FALLBACKS",
    )
    imagen_model: str = Field(default="imagen-4.0-generate-001", validation_alias="DEFAULT_IMAGEN_MODEL")
    tts_model: str = Field(default="gemini-2.5-flash-preview-tts", validation_alias="DEFAULT_TTS_MODEL")
    video_model: str = Field(default="veo-3.1-fast-generate-preview", validation_alias="DEFAULT_VIDEO_MODEL")
    default_gemini_voice: str = Field(default="Kore", validation_alias="DEFAULT_GEMINI_VOICE")
    default_voice_language: str = Field(default="en-US", validation_alias="DEFAULT_VOICE_LANGUAGE")

    # Video generation is opt-in: Veo requires a paid, allow-listed key (§29)
    video_generation_enabled: bool = Field(default=False, validation_alias="VIDEO_GENERATION_ENABLED")

    # Timeouts (seconds) — every external call is bounded (§50)
    image_timeout: float = Field(default=120.0, validation_alias="IMAGE_TIMEOUT")
    audio_timeout: float = Field(default=60.0, validation_alias="AUDIO_TIMEOUT")
    video_timeout: float = Field(default=180.0, validation_alias="VIDEO_TIMEOUT")
    video_poll_interval: float = Field(default=10.0, validation_alias="VIDEO_POLL_INTERVAL")
    video_poll_timeout: float = Field(default=900.0, validation_alias="VIDEO_POLL_TIMEOUT")

    # Retry policy (§51)
    retry_max_attempts: int = Field(default=3, validation_alias="RETRY_MAX_ATTEMPTS")
    retry_base_delay: float = Field(default=2.0, validation_alias="RETRY_BASE_DELAY")
    retry_max_delay: float = Field(default=60.0, validation_alias="RETRY_MAX_DELAY")
    quota_cooldown_seconds: int = Field(default=900, validation_alias="QUOTA_COOLDOWN_SECONDS")
    rate_limit_cooldown_seconds: int = Field(default=60, validation_alias="RATE_LIMIT_COOLDOWN_SECONDS")

    # Upload limits (§52 — validate file uploads)
    max_upload_size_mb: int = Field(default=50, validation_alias="MAX_UPLOAD_SIZE_MB")

    # Merge still image + voiceover into an MP4 when FFmpeg is present
    merge_enabled: bool = Field(default=True, validation_alias="MERGE_ENABLED")

    # Serverless hosts (Vercel/Lambda) kill the process once a response is sent
    # and give every invocation a fresh empty /tmp. Two behaviours must change:
    #   * startup reconciliation must NOT run — it would see an empty media dir
    #     and delete every asset row in the database;
    #   * batch generation must be refused rather than started and silently
    #     killed mid-run.
    serverless_mode: bool = Field(default=False, validation_alias="SERVERLESS_MODE")

    # FFmpeg
    ffmpeg_path: str = Field(default="ffmpeg", validation_alias="FFMPEG_PATH")

    model_config = SettingsConfigDict(
        env_file=os.path.join(base_dir, ".env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        # Vercel/AWS set these automatically; no manual flag needed.
        if os.environ.get("VERCEL") or os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
            self.serverless_mode = True

        def absolute(path: str) -> str:
            return os.path.normpath(path if os.path.isabs(path) else os.path.join(self.base_dir, path))

        self.output_dir = absolute(self.output_dir)
        self.uploads_dir = absolute(self.uploads_dir)
        self.images_dir = os.path.join(self.output_dir, "images")
        self.audio_dir = os.path.join(self.output_dir, "audio")
        self.videos_dir = os.path.join(self.output_dir, "videos")
        self.merged_dir = os.path.join(self.output_dir, "merged")

        for directory in (
            self.output_dir, self.images_dir, self.audio_dir,
            self.videos_dir, self.merged_dir, self.uploads_dir,
        ):
            os.makedirs(directory, exist_ok=True)

        # Startup validation
        if not self.supabase_url or not self.supabase_anon_key:
            raise ValueError("SUPABASE_URL and SUPABASE_KEY must be configured in .env")
        if not self.encryption_key or len(self.encryption_key) < 32:
            import warnings

            warnings.warn(
                "ENCRYPTION_KEY is missing or shorter than 32 characters. "
                "API keys cannot be stored securely until this is set."
            )

settings = Settings()
