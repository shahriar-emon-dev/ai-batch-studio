import asyncio
import os
import logging
from backend.config import settings

logger = logging.getLogger(__name__)

async def merge_image_audio(image_path: str, audio_path: str, output_path: str) -> str:
    """Merges an image and audio file into an MP4 video using FFmpeg."""
    if not os.path.exists(image_path):
        raise FileNotFoundError(f"Image not found: {image_path}")
    if not os.path.exists(audio_path):
        raise FileNotFoundError(f"Audio not found: {audio_path}")
        
    cmd = [
        settings.ffmpeg_path,
        "-y",  # Overwrite output
        "-loop", "1",
        "-i", image_path,
        "-i", audio_path,
        "-c:v", "libx264",
        "-tune", "stillimage",
        "-c:a", "aac",
        "-b:a", "192k",
        "-pix_fmt", "yuv420p",
        "-shortest",
        output_path
    ]
    
    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    
    stdout, stderr = await process.communicate()
    
    if process.returncode != 0:
        logger.error(f"FFmpeg failed with return code {process.returncode}")
        logger.error(f"Stderr: {stderr.decode()}")
        raise Exception(f"FFmpeg failed: {stderr.decode()}")
        
    return output_path
