import os
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

class Settings(BaseSettings):
    # Supabase config
    supabase_url: str = Field(default="", env="SUPABASE_URL")
    supabase_anon_key: str = Field(default="", env="SUPABASE_KEY")
    supabase_service_role_key: str = Field(default="", env="SUPABASE_SERVICE_ROLE_KEY")
    supabase_jwt_secret: str = Field(default="", env="SUPABASE_JWT_SECRET")

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

settings = Settings()
