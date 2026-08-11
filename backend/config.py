import os
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

class Settings(BaseSettings):
    # Supabase config
    supabase_url: str = Field(default="", env="SUPABASE_URL")
    supabase_anon_key: str = Field(default="", env="SUPABASE_KEY")
    supabase_service_role_key: str = Field(default="", env="SUPABASE_SERVICE_ROLE_KEY")
    supabase_jwt_secret: str = Field(default="", env="SUPABASE_JWT_SECRET")

    # Security
    encryption_key: str = Field(default="", env="ENCRYPTION_KEY")
    allowed_origins: str = Field(default="http://localhost:8000,http://127.0.0.1:8000,http://localhost:3000", env="ALLOWED_ORIGINS")

    # Paths
    base_dir: str = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    output_dir: str = os.path.join(base_dir, "output")
    images_dir: str = os.path.join(output_dir, "images")
    audio_dir: str = os.path.join(output_dir, "audio")
    videos_dir: str = os.path.join(output_dir, "videos")
    merged_dir: str = os.path.join(output_dir, "merged")
    uploads_dir: str = os.path.join(base_dir, "uploads")

    # API Defaults
    default_concurrency: int = Field(default=3, env="MAX_CONCURRENT_JOBS")
    
    # FFmpeg
    ffmpeg_path: str = Field(default="ffmpeg", env="FFMPEG_PATH")

    model_config = SettingsConfigDict(
        env_file=os.path.join(base_dir, ".env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Ensure directories exist
        for d in [self.output_dir, self.images_dir, self.audio_dir, self.videos_dir, self.merged_dir, self.uploads_dir]:
            os.makedirs(d, exist_ok=True)
            
        # Startup Validation
        if not self.supabase_url or not self.supabase_anon_key:
            raise ValueError("CRITICAL ERROR: SUPABASE_URL and SUPABASE_ANON_KEY must be configured in .env")
        if not self.encryption_key or len(self.encryption_key) < 32:
            import warnings
            warnings.warn("ENCRYPTION_KEY is missing or too short. API Keys will not be secure!")

settings = Settings()
