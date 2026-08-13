import asyncio
import logging
import os
import shutil
from typing import Optional

from backend.config import settings

logger = logging.getLogger(__name__)

_available: Optional[bool] = None


def is_available() -> bool:
    """Cached FFmpeg presence check — a missing binary makes merges UNSUPPORTED,
    not FAILED (proposal §29, §33)."""
    global _available
    if _available is None:
        path = settings.ffmpeg_path
        _available = bool(shutil.which(path) or (os.path.isfile(path) and os.access(path, os.X_OK)))
        if not _available:
            logger.warning("FFmpeg not found at '%s' — video merging is unavailable", path)
    return _available


async def _run(cmd: list, description: str) -> None:
    process = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await process.communicate()
    if process.returncode != 0:
        detail = stderr.decode(errors="replace")[-800:]
        logger.error("%s failed (exit %s): %s", description, process.returncode, detail)
        raise RuntimeError(f"{description} failed: {detail}")


async def merge_image_audio(image_path: str, audio_path: str, output_path: str) -> str:
    """Create an MP4 from a still image and a voiceover track."""
    if not is_available():
        raise FileNotFoundError("FFmpeg is not installed or not on PATH")
    if not os.path.exists(image_path):
        raise FileNotFoundError(f"Image not found: {image_path}")
    if not os.path.exists(audio_path):
        raise FileNotFoundError(f"Audio not found: {audio_path}")

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    cmd = [
        settings.ffmpeg_path,
        "-y",
        "-loop", "1",
        "-i", image_path,
        "-i", audio_path,
        "-c:v", "libx264",
        "-tune", "stillimage",
        "-c:a", "aac",
        "-b:a", "192k",
        "-pix_fmt", "yuv420p",
        # Even dimensions are required by libx264.
        "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
        "-shortest",
        output_path,
    ]
    await _run(cmd, "FFmpeg image+audio merge")
    return output_path


async def merge_video_audio(video_path: str, audio_path: str, output_path: str) -> str:
    """Attach a voiceover track to a generated video."""
    if not is_available():
        raise FileNotFoundError("FFmpeg is not installed or not on PATH")
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video not found: {video_path}")
    if not os.path.exists(audio_path):
        raise FileNotFoundError(f"Audio not found: {audio_path}")

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    cmd = [
        settings.ffmpeg_path,
        "-y",
        "-i", video_path,
        "-i", audio_path,
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "192k",
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-shortest",
        output_path,
    ]
    await _run(cmd, "FFmpeg video+audio merge")
    return output_path
