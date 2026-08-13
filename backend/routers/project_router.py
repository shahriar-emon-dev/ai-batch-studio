"""Project + scene management API (proposal §30, §31, §54, §55)."""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.auth import get_token, verify_token
from backend.database import get_db_client
from backend.services import asset_service, task_service
from backend.services.generation_service import is_running

logger = logging.getLogger(__name__)
router = APIRouter()

SCENE_COLUMNS = (
    "scene_number", "visual_prompt", "video_prompt", "voiceover_script", "master_prompt",
    "negative_prompt", "style", "tone", "voice_name", "language", "duration",
    "aspect_ratio", "media_type", "filename",
)

# What the project workspace actually renders. Kept narrow on purpose: the
# generation pipeline reads full scene rows separately, so nothing downstream
# depends on this list.
SCENE_LIST_COLUMNS = (
    "id, scene_number, visual_prompt, voiceover_script, aspect_ratio, media_type, filename, "
    "visual_status, voice_status, video_status, merge_status, overall_status, error_message, "
    "visual_path, audio_path, video_path, merged_path"
)


class ProjectCreate(BaseModel):
    name: str
    mode: Optional[str] = "IMAGE_VOICE"
    description: Optional[str] = ""
    scenes: Optional[List[Dict[str, Any]]] = []
    csv_file_id: Optional[int] = None


class ScenesImportRequest(BaseModel):
    scenes: List[Dict[str, Any]]
    replace: bool = False
    csv_file_id: Optional[int] = None


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    mode: Optional[str] = None


def _scene_payload(scene: Dict[str, Any], index: int, project_id: int, user_id: str,
                   csv_file_id: Optional[int]) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "project_id": project_id,
        "user_id": user_id,
        "scene_number": str(scene.get("scene_number") or scene.get("id") or (index + 1)),
        "visual_prompt": scene.get("visual_prompt") or "",
        "voiceover_script": scene.get("voiceover_script") or "",
        "custom_metadata": scene.get("custom_metadata") or {},
        "overall_status": "PENDING",
        "visual_status": "PENDING",
        "voice_status": "PENDING",
        "video_status": "PENDING",
    }

    for column in SCENE_COLUMNS:
        value = scene.get(column)
        if value not in (None, ""):
            payload[column] = value

    payload.setdefault("aspect_ratio", "16:9")
    payload.setdefault("media_type", "image")
    payload.setdefault("filename", f"scene_{index + 1:03d}")
    if csv_file_id:
        payload["csv_file_id"] = csv_file_id

    duration = payload.get("duration")
    if duration is not None:
        try:
            payload["duration"] = float(duration)
        except (TypeError, ValueError):
            payload.pop("duration", None)
    return payload


def _require_project(client, project_id: int) -> Dict[str, Any]:
    result = client.table("projects").select("*").eq("id", project_id).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Project not found")
    return result.data[0]


@router.get("")
async def list_projects(token: str = Depends(get_token), user_id: str = Depends(verify_token)):
    client = get_db_client(token)
    projects = (
        client.table("projects")
        .select("id, name, description, mode, status, total_scenes, completed_scenes, failed_scenes, skipped_scenes, created_at, updated_at, started_at, finished_at")
        .order("created_at", desc=True)
        .execute()
        .data
        or []
    )
    for project in projects:
        project["worker_active"] = is_running(project["id"])
    return projects


@router.get("/stats")
async def get_dashboard_stats(token: str = Depends(get_token), user_id: str = Depends(verify_token)):
    """Dashboard counters, all derived from real rows (§53, §54)."""
    client = get_db_client(token)

    def safe_select(table: str, columns: str) -> List[Dict[str, Any]]:
        """One stale table must not take the whole dashboard down (§19)."""
        try:
            return client.table(table).select(columns).eq("user_id", user_id).execute().data or []
        except Exception as exc:
            logger.warning("Dashboard stats: could not read %s (%s)", table, exc)
            return []

    projects = safe_select("projects", "id, status, completed_scenes, failed_scenes, total_scenes")
    assets = safe_select("assets", "asset_type")
    tasks = safe_select("generation_tasks", "status")
    profiles = safe_select("api_profiles", "id, is_active")

    def assets_of(asset_type: str) -> int:
        return sum(1 for a in assets if a.get("asset_type") == asset_type)

    return {
        "total_projects": len(projects),
        "active_jobs": sum(1 for p in projects if is_running(p["id"])),
        "completed_scenes": sum(p.get("completed_scenes") or 0 for p in projects),
        "failed_scenes": sum(p.get("failed_scenes") or 0 for p in projects),
        "total_scenes": sum(p.get("total_scenes") or 0 for p in projects),
        "processing_tasks": sum(1 for t in tasks if t.get("status") in task_service.ACTIVE_STATUSES),
        "pending_tasks": sum(
            1 for t in tasks if t.get("status") in (task_service.STATUS_PENDING, task_service.STATUS_QUEUED)
        ),
        "images_generated": assets_of("image"),
        "voiceovers_generated": assets_of("voiceover"),
        "videos_generated": assets_of("video") + assets_of("merged"),
        "api_configured": any(p.get("is_active") for p in profiles),
        "api_profile_count": len(profiles),
    }


@router.post("")
async def create_project(project: ProjectCreate, token: str = Depends(get_token), user_id: str = Depends(verify_token)):
    client = get_db_client(token)

    if not (project.name or "").strip():
        raise HTTPException(status_code=400, detail="Project name is required")

    created = (
        client.table("projects")
        .insert(
            {
                "name": project.name.strip(),
                "user_id": user_id,
                "mode": project.mode or "IMAGE_VOICE",
                "description": project.description or "",
                "status": "PENDING",
                "total_scenes": len(project.scenes or []),
            }
        )
        .execute()
    )
    if not created.data:
        raise HTTPException(status_code=500, detail="Failed to create project")

    project_id = created.data[0]["id"]

    if project.scenes:
        client.table("scenes").insert(
            [
                _scene_payload(scene, index, project_id, user_id, project.csv_file_id)
                for index, scene in enumerate(project.scenes)
            ]
        ).execute()

    return {"id": project_id, "name": project.name, "project": created.data[0]}


@router.post("/{project_id}/scenes")
async def import_scenes(
    project_id: int,
    req: ScenesImportRequest,
    token: str = Depends(get_token),
    user_id: str = Depends(verify_token),
):
    """Import scenes. `replace=true` swaps the whole scene set for this project."""
    client = get_db_client(token)
    _require_project(client, project_id)

    if is_running(project_id):
        raise HTTPException(status_code=409, detail="Cannot modify scenes while generation is running")

    if req.replace:
        # Assets and tasks cascade from scenes; stored files are removed too so
        # the workspace does not keep orphaned media.
        asset_service.delete_assets_for_project(project_id)
        client.table("scenes").delete().eq("project_id", project_id).execute()

    if req.scenes:
        client.table("scenes").insert(
            [
                _scene_payload(scene, index, project_id, user_id, req.csv_file_id)
                for index, scene in enumerate(req.scenes)
            ]
        ).execute()

    total = len(client.table("scenes").select("id").eq("project_id", project_id).execute().data or [])
    updates: Dict[str, Any] = {"total_scenes": total}
    if req.replace:
        updates.update(
            {"completed_scenes": 0, "failed_scenes": 0, "skipped_scenes": 0, "status": "PENDING"}
        )
    client.table("projects").update(updates).eq("id", project_id).execute()

    return {"status": "ok", "imported_count": len(req.scenes), "total_scenes": total, "replaced": req.replace}


@router.get("/{project_id}")
async def get_project(project_id: int, token: str = Depends(get_token), user_id: str = Depends(verify_token)):
    client = get_db_client(token)
    project = _require_project(client, project_id)

    # Only the fields the workspace renders. `select("*")` also pulls
    # custom_metadata, master/negative/enhanced prompts and timestamps, which
    # roughly doubles both query time and payload for no visible benefit.
    project["scenes"] = (
        client.table("scenes")
        .select(SCENE_LIST_COLUMNS)
        .eq("project_id", project_id)
        .order("id")
        .execute()
        .data
        or []
    )
    project["worker_active"] = is_running(project_id)
    project["csv_files"] = (
        client.table("csv_files")
        .select("id, filename, file_size_bytes, encoding, delimiter, row_count, column_count, valid_row_count, invalid_row_count, status, created_at")
        .eq("project_id", project_id)
        .order("id", desc=True)
        .execute()
        .data
        or []
    )
    return project


@router.patch("/{project_id}")
async def update_project(
    project_id: int, req: ProjectUpdate, token: str = Depends(get_token), user_id: str = Depends(verify_token)
):
    client = get_db_client(token)
    _require_project(client, project_id)

    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    if not updates:
        return {"status": "no_change"}

    result = client.table("projects").update(updates).eq("id", project_id).execute()
    return {"status": "ok", "project": (result.data or [None])[0]}


@router.delete("/{project_id}")
async def delete_project(project_id: int, token: str = Depends(get_token), user_id: str = Depends(verify_token)):
    client = get_db_client(token)
    _require_project(client, project_id)

    if is_running(project_id):
        raise HTTPException(status_code=409, detail="Cancel the running generation before deleting this project")

    asset_service.delete_assets_for_project(project_id)
    client.table("projects").delete().eq("id", project_id).execute()
    return {"success": True}
