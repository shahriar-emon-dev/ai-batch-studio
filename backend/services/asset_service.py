"""Asset storage + registration (proposal §34, §41, §42, §44).

Media binaries live on the configured object/media storage (the `output/`
volume served by the API), never inside a Supabase table. Supabase only holds
the reference and metadata.

`register_asset` is the single place that decides an asset is genuinely
complete: the bytes must be on disk and non-empty before the row is written.
That row is what the UI's blue checkmark reads (§34).
"""

import datetime
import hashlib
import hmac
import logging
import os
import re
from typing import Any, Dict, Optional, Tuple

from backend.config import settings
from backend.database import get_admin_client

logger = logging.getLogger(__name__)

ASSET_DIRS = {
    "image": settings.images_dir,
    "voiceover": settings.audio_dir,
    "video": settings.videos_dir,
    "merged": settings.merged_dir,
}

ASSET_URL_PREFIX = {
    "image": "/output/images",
    "voiceover": "/output/audio",
    "video": "/output/videos",
    "merged": "/output/merged",
}

MIME_TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "mp4": "video/mp4",
}


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def safe_filename(value: str, fallback: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", (value or "").strip())
    cleaned = cleaned.strip("._-")
    return cleaned or fallback


def storage_token(asset_type: str, scene_id: Any) -> str:
    """Unguessable but stable per-asset URL component (§46, §52).

    Media is served as static files so `<img src>` works, which means the URL
    itself is the only thing standing between one user's assets and another's.
    Sequential names like `12.png` are trivially enumerable, so the key is an
    HMAC of the scene id under the server secret: stable across re-runs (so
    regeneration still overwrites in place) but not derivable by a client.
    """
    secret = (settings.encryption_key or "").encode("utf-8")
    if not secret:
        # Without a secret we cannot make the name unguessable; the startup
        # warning about ENCRYPTION_KEY already covers this case.
        return ""
    digest = hmac.new(secret, f"{asset_type}:{scene_id}".encode("utf-8"), hashlib.sha256)
    return digest.hexdigest()[:16]


def storage_paths(asset_type: str, scene_id: Any, extension: str) -> Tuple[str, str]:
    """Return (absolute_path, public_url) for a scene's asset of this type.

    The storage key is derived from the scene id so re-running a job overwrites
    in place instead of accumulating orphans (§32 idempotency).
    """
    directory = ASSET_DIRS.get(asset_type)
    if not directory:
        raise ValueError(f"Unknown asset type: {asset_type}")
    token = storage_token(asset_type, scene_id)
    filename = f"{scene_id}_{token}.{extension}" if token else f"{scene_id}.{extension}"
    return os.path.join(directory, filename), f"{ASSET_URL_PREFIX[asset_type]}/{filename}"


def _candidate_paths(asset_type: str, scene_id: Any, extension: str) -> Tuple[Tuple[str, str], ...]:
    """Current key first, then the pre-token name so existing media still resolves."""
    directory = ASSET_DIRS[asset_type]
    prefix = ASSET_URL_PREFIX[asset_type]
    token = storage_token(asset_type, scene_id)

    names = []
    if token:
        names.append(f"{scene_id}_{token}.{extension}")
    names.append(f"{scene_id}.{extension}")
    return tuple((os.path.join(directory, name), f"{prefix}/{name}") for name in names)


def write_asset_file(asset_type: str, scene_id: Any, extension: str, data: bytes) -> Tuple[str, str]:
    """Persist bytes to media storage. Returns (absolute_path, public_url)."""
    if not data:
        raise ValueError("Refusing to store an empty asset")

    absolute, url = storage_paths(asset_type, scene_id, extension)
    os.makedirs(os.path.dirname(absolute), exist_ok=True)
    with open(absolute, "wb") as handle:
        handle.write(data)

    _remove_other_extensions(asset_type, scene_id, keep=absolute)
    return absolute, url


def _remove_other_extensions(asset_type: str, scene_id: Any, keep: str) -> None:
    """Drop this scene's earlier files of the same type in another format.

    A voiceover first produced as .mp3 by Cloud TTS and later regenerated as
    .wav by the Gemini fallback would otherwise leave the stale .mp3 on disk,
    where the idempotency lookup would keep finding it first.
    """
    directory = ASSET_DIRS[asset_type]
    prefixes = (f"{scene_id}_{storage_token(asset_type, scene_id)}.", f"{scene_id}.")
    keep_name = os.path.basename(keep)

    try:
        for name in os.listdir(directory):
            if name != keep_name and any(name.startswith(p) for p in prefixes if not p.endswith("_.")):
                try:
                    os.remove(os.path.join(directory, name))
                except OSError as exc:
                    logger.debug("Could not remove stale asset %s: %s", name, exc)
    except OSError as exc:
        logger.debug("Could not scan %s for stale assets: %s", directory, exc)


def stored_asset_path(asset_type: str, scene_id: Any, extensions) -> Optional[Tuple[str, str]]:
    """Find an already-generated file for this scene, if any."""
    for extension in extensions:
        for absolute, url in _candidate_paths(asset_type, scene_id, extension):
            if os.path.exists(absolute) and os.path.getsize(absolute) > 0:
                return absolute, url
    return None


def newest_mtime(asset_type: str, scene_id: Any, extensions) -> float:
    """Modification time of a scene's stored asset, or 0.0 when absent."""
    found = stored_asset_path(asset_type, scene_id, extensions)
    return os.path.getmtime(found[0]) if found else 0.0


def register_asset(
    user_id: Optional[str],
    project_id: Any,
    scene_id: Any,
    asset_type: str,
    absolute_path: str,
    public_url: str,
    *,
    scene_number: Any = None,
    task_id: Any = None,
    display_filename: str = "",
    prompt: str = "",
    model: str = "",
    provider: str = "google",
) -> Optional[Dict[str, Any]]:
    """Verify the stored output, then upsert the assets row.

    Returns the stored row, or None when verification fails — callers must
    treat None as "not completed".
    """
    if not os.path.exists(absolute_path):
        logger.error("Asset verification failed: %s is missing", absolute_path)
        return None

    size = os.path.getsize(absolute_path)
    if size <= 0:
        logger.error("Asset verification failed: %s is empty", absolute_path)
        return None

    extension = os.path.splitext(absolute_path)[1].lstrip(".").lower()
    record = {
        "user_id": user_id,
        "project_id": int(project_id) if str(project_id).isdigit() else None,
        "scene_id": int(scene_id) if str(scene_id).isdigit() else None,
        "scene_number": str(scene_number) if scene_number is not None else None,
        "task_id": int(task_id) if task_id and str(task_id).isdigit() else None,
        "asset_type": asset_type,
        "storage_path": public_url,
        "filename": display_filename or os.path.basename(absolute_path),
        "mime_type": MIME_TYPES.get(extension, "application/octet-stream"),
        "size": size,
        "provider": provider,
        "model": model or None,
        "prompt": (prompt or "")[:5000] or None,
        "verified": True,
        "updated_at": _now_iso(),
    }

    client = get_admin_client()
    if not client:
        logger.warning("No database client; asset %s stored but not registered", public_url)
        return record

    try:
        result = (
            client.table("assets")
            .upsert(record, on_conflict="scene_id,asset_type")
            .execute()
        )
        return (result.data or [record])[0]
    except Exception as exc:
        logger.error("Failed to register asset %s: %s", public_url, exc)
        return None


def delete_assets_for_project(project_id: Any) -> int:
    """Remove stored files *and* asset rows for a project.

    `assets.scene_id` is ON DELETE SET NULL, so deleting scenes alone would
    leave rows with a null scene behind and they would keep showing up in the
    asset browser. Both sides are cleaned here.
    """
    client = get_admin_client()
    if not client:
        return 0

    removed = 0
    try:
        rows = (
            client.table("assets")
            .select("storage_path")
            .eq("project_id", int(project_id))
            .execute()
            .data
            or []
        )
        for row in rows:
            absolute = local_path_for_url(row.get("storage_path"))
            if absolute and os.path.exists(absolute):
                try:
                    os.remove(absolute)
                    removed += 1
                except OSError as exc:
                    logger.debug("Could not delete %s: %s", absolute, exc)

        client.table("assets").delete().eq("project_id", int(project_id)).execute()
    except Exception as exc:
        logger.warning("Asset cleanup for project %s incomplete: %s", project_id, exc)
    return removed


def purge_orphaned_assets(user_id: str) -> int:
    """Drop asset rows whose scene no longer exists (defensive cleanup)."""
    client = get_admin_client()
    if not client:
        return 0
    try:
        result = client.table("assets").delete().is_("scene_id", "null").eq("user_id", user_id).execute()
        return len(result.data or [])
    except Exception as exc:
        logger.debug("Orphan purge skipped: %s", exc)
        return 0


def local_path_for_url(public_url: Optional[str]) -> Optional[str]:
    """Map `/output/images/12.png` back to its absolute path on disk."""
    if not public_url:
        return None
    relative = str(public_url).lstrip("/")
    if relative.startswith("output/"):
        relative = relative[len("output/"):]
    if not relative:
        return None
    candidate = os.path.normpath(os.path.join(settings.output_dir, relative.replace("/", os.sep)))
    # Never let a stored value escape the media directory.
    if not candidate.startswith(os.path.normpath(settings.output_dir)):
        return None
    return candidate
