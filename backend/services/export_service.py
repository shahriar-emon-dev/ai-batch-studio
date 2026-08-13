"""Selective export packaging (proposal §43, §44).

Exports are built from verified `assets` rows, organised into the folder layout
the proposal describes, with a metadata manifest alongside the media.
"""

import csv
import io
import json
import logging
import os
import time
import uuid
import zipfile
from typing import Any, Dict, Iterable, List, Optional

from backend.config import settings
from backend.services.asset_service import local_path_for_url

logger = logging.getLogger(__name__)

FOLDER_FOR_TYPE = {
    "image": "images",
    "voiceover": "voiceovers",
    "video": "videos",
    "merged": "videos",
}

# Export requests use plural, user-facing names.
TYPE_FOR_FILTER = {
    "images": {"image"},
    "voiceovers": {"voiceover"},
    "videos": {"video", "merged"},
}

EXPORT_RETENTION_SECONDS = 24 * 60 * 60


def _safe_name(value: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in (value or "export"))[:60] or "export"


def filter_asset_types(file_types: Optional[List[str]]) -> Optional[set]:
    if not file_types:
        return None
    wanted = set()
    for entry in file_types:
        wanted |= TYPE_FOR_FILTER.get(str(entry).lower(), {str(entry).lower()})
    return wanted


def cleanup_old_exports() -> None:
    """Remove ZIP/report artifacts older than a day so output/ does not grow forever."""
    cutoff = time.time() - EXPORT_RETENTION_SECONDS
    try:
        for name in os.listdir(settings.output_dir):
            if not (name.startswith("export_") or name.startswith("report_")):
                continue
            path = os.path.join(settings.output_dir, name)
            if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                os.remove(path)
    except OSError as exc:
        logger.debug("Export cleanup skipped: %s", exc)


async def create_export_zip(
    project_name: str,
    assets: Iterable[Dict[str, Any]],
    scenes: Optional[List[Dict[str, Any]]] = None,
    organize_by: str = "type",
) -> Dict[str, Any]:
    """Package the given assets. Returns download URL and what was included."""
    cleanup_old_exports()

    root = _safe_name(project_name)
    zip_name = f"export_{root}_{uuid.uuid4().hex[:8]}.zip"
    zip_path = os.path.join(settings.output_dir, zip_name)

    included: List[Dict[str, Any]] = []
    missing: List[str] = []
    used_names: set = set()

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for asset in assets:
            source = local_path_for_url(asset.get("storage_path"))
            if not source or not os.path.exists(source):
                missing.append(asset.get("storage_path") or "unknown")
                continue

            asset_type = asset.get("asset_type") or "image"
            filename = asset.get("filename") or os.path.basename(source)

            if organize_by == "scene":
                folder = f"scene_{asset.get('scene_number') or asset.get('scene_id')}"
            else:
                folder = FOLDER_FOR_TYPE.get(asset_type, "other")

            arcname = f"{root}/{folder}/{filename}"
            # Two scenes can share a CSV filename — keep both.
            if arcname in used_names:
                stem, extension = os.path.splitext(filename)
                arcname = f"{root}/{folder}/{stem}_{asset.get('scene_id')}{extension}"
            used_names.add(arcname)

            archive.write(source, arcname)
            included.append(
                {
                    "scene_number": asset.get("scene_number"),
                    "scene_id": asset.get("scene_id"),
                    "asset_type": asset_type,
                    "filename": filename,
                    "archive_path": arcname,
                    "size": asset.get("size"),
                    "prompt": asset.get("prompt"),
                }
            )

        manifest = {
            "project": project_name,
            "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "asset_count": len(included),
            "assets": included,
            "missing_files": missing,
        }
        archive.writestr(f"{root}/metadata/manifest.json", json.dumps(manifest, indent=2))

        if scenes:
            archive.writestr(f"{root}/metadata/scenes.csv", _scenes_csv(scenes))

    return {
        "download_url": f"/output/{zip_name}",
        "filename": zip_name,
        "asset_count": len(included),
        "missing_count": len(missing),
        "assets": included,
    }


REPORT_FIELDS = [
    "scene_number",
    "media_type",
    "visual_prompt",
    "voiceover_script",
    "aspect_ratio",
    "style",
    "tone",
    "overall_status",
    "visual_status",
    "voice_status",
    "video_status",
    "merge_status",
    "visual_path",
    "audio_path",
    "video_path",
    "merged_path",
    "error_message",
    "retry_count",
    "created_at",
    "updated_at",
]


def _scenes_csv(scenes: List[Dict[str, Any]]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=REPORT_FIELDS, extrasaction="ignore")
    writer.writeheader()
    for scene in scenes:
        writer.writerow({field: scene.get(field, "") if scene.get(field) is not None else "" for field in REPORT_FIELDS})
    return buffer.getvalue()


async def create_generation_report_csv(project_name: str, scenes: List[Dict[str, Any]]) -> str:
    """Write a per-scene status report to media storage and return its URL."""
    cleanup_old_exports()
    filename = f"report_{_safe_name(project_name)}_{uuid.uuid4().hex[:8]}.csv"
    path = os.path.join(settings.output_dir, filename)
    with open(path, "w", newline="", encoding="utf-8") as handle:
        handle.write(_scenes_csv(scenes))
    return f"/output/{filename}"
