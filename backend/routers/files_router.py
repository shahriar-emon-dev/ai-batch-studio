"""File listing, download and selective export API (proposal §43, §44)."""

import logging
import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from backend.auth import get_token, verify_token
from backend.database import get_db_client
from backend.services.asset_service import local_path_for_url
from backend.services.export_service import (
    create_export_zip,
    create_generation_report_csv,
    filter_asset_types,
)

logger = logging.getLogger(__name__)
router = APIRouter()


class ExportRequest(BaseModel):
    project_id: Optional[str] = None
    status_filter: Optional[str] = "all"          # all | completed | failed
    scene_ids: Optional[List[int]] = None         # select individual scenes (§43)
    asset_ids: Optional[List[int]] = None         # select individual files (§43)
    file_types: Optional[List[str]] = None        # images | voiceovers | videos
    organize_by: str = "type"                     # type | scene (§44)


def _as_project_id(value: Any) -> int:
    """Reject a non-numeric project id with a 400 instead of a 500 from the DB."""
    try:
        return int(value)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"Invalid project_id: {value!r}")


def _collect_assets(client, req: ExportRequest, user_id: str) -> List[Dict[str, Any]]:
    query = client.table("assets").select("*").eq("user_id", user_id)

    if req.asset_ids:
        query = query.in_("id", req.asset_ids)
    else:
        if not req.project_id:
            raise HTTPException(status_code=400, detail="project_id is required unless asset_ids are given")
        query = query.eq("project_id", _as_project_id(req.project_id))
        if req.scene_ids:
            query = query.in_("scene_id", req.scene_ids)

    assets = query.execute().data or []

    wanted_types = filter_asset_types(req.file_types)
    if wanted_types:
        assets = [a for a in assets if a.get("asset_type") in wanted_types]

    status = (req.status_filter or "all").lower()
    if status != "all" and assets:
        scene_ids = list({a["scene_id"] for a in assets if a.get("scene_id")})
        scenes = (
            client.table("scenes")
            .select("id, overall_status")
            .in_("id", scene_ids)
            .execute()
            .data
            or []
        )
        allowed = {s["id"] for s in scenes if (s.get("overall_status") or "").lower() == status}
        assets = [a for a in assets if a.get("scene_id") in allowed]

    return assets


@router.post("")
@router.post("/export")
async def export_files(
    req: ExportRequest, token: str = Depends(get_token), user_id: str = Depends(verify_token)
):
    """Build a ZIP from the selected assets, scenes, folders or whole project."""
    client = get_db_client(token)

    project_name = "Project"
    scenes: List[Dict[str, Any]] = []
    if req.project_id:
        project_id = _as_project_id(req.project_id)
        project = client.table("projects").select("name").eq("id", project_id).execute()
        if not project.data:
            raise HTTPException(status_code=404, detail="Project not found")
        project_name = project.data[0]["name"]
        scenes = (
            client.table("scenes").select("*").eq("project_id", project_id).order("id").execute().data or []
        )

    assets = _collect_assets(client, req, user_id)
    if not assets:
        raise HTTPException(
            status_code=400,
            detail="No generated assets match this selection. Generate assets before exporting.",
        )

    result = await create_export_zip(project_name, assets, scenes, req.organize_by)
    return {
        "download_url": result["download_url"],
        "filename": result["filename"],
        "asset_count": result["asset_count"],
        "missing_count": result["missing_count"],
        # Field kept for the existing export page.
        "exported_scenes_count": len({a.get("scene_id") for a in assets}),
    }


@router.get("/report/{project_id}")
async def download_generation_report(project_id: int, token: str = Depends(get_token), user_id: str = Depends(verify_token)):
    """Per-scene CSV report of statuses, paths, errors and timestamps (§58)."""
    client = get_db_client(token)

    project = client.table("projects").select("name").eq("id", project_id).execute()
    if not project.data:
        raise HTTPException(status_code=404, detail="Project not found")

    scenes = client.table("scenes").select("*").eq("project_id", project_id).order("id").execute().data or []
    if not scenes:
        raise HTTPException(status_code=400, detail="No scenes found for this project")

    url = await create_generation_report_csv(project.data[0]["name"], scenes)
    return {"download_url": url, "scene_count": len(scenes), "project_name": project.data[0]["name"]}


@router.get("/{project_id}")
async def list_files(project_id: int, token: str = Depends(get_token), user_id: str = Depends(verify_token)):
    """Scenes plus their verified assets — what the export planner selects from."""
    client = get_db_client(token)

    # `select("*")` rather than naming columns: a database that predates one of
    # them must not take this endpoint down (§19).
    scene_fields = ("id", "scene_number", "visual_prompt", "voiceover_script", "media_type",
                    "overall_status", "visual_path", "audio_path", "video_path", "merged_path")
    scenes = [
        {field: row.get(field) for field in scene_fields}
        for row in (
            client.table("scenes").select("*").eq("project_id", project_id).order("id").execute().data or []
        )
    ]
    assets = (
        client.table("assets")
        .select("*")
        .eq("project_id", project_id)
        .eq("user_id", user_id)
        .execute()
        .data
        or []
    )

    by_scene: Dict[Any, List[Dict[str, Any]]] = {}
    for asset in assets:
        by_scene.setdefault(asset.get("scene_id"), []).append(asset)

    for scene in scenes:
        scene["assets"] = by_scene.get(scene["id"], [])

    return {"scenes": scenes, "asset_count": len(assets)}


@router.get("/download/{scene_id}/{asset_type}")
async def download_scene_asset(
    scene_id: int, asset_type: str, token: str = Depends(get_token), user_id: str = Depends(verify_token)
):
    client = get_db_client(token)
    types = ["video", "merged"] if asset_type == "video" else [asset_type]
    result = (
        client.table("assets")
        .select("*")
        .eq("scene_id", scene_id)
        .eq("user_id", user_id)
        .in_("asset_type", types)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="No such asset for this scene")

    asset = result.data[0]
    path = local_path_for_url(asset.get("storage_path"))
    if not path or not os.path.exists(path):
        raise HTTPException(status_code=410, detail="The stored file is no longer available")

    return FileResponse(
        path,
        media_type=asset.get("mime_type") or "application/octet-stream",
        filename=asset.get("filename") or os.path.basename(path),
    )
