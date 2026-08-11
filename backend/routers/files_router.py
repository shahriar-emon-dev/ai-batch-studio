from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List, Optional
from backend.auth import get_token, verify_token
from backend.database import get_db_client
from backend.services.export_service import create_filtered_export_zip, create_generation_report_csv

router = APIRouter()

class ExportFilterRequest(BaseModel):
    project_id: str
    status_filter: Optional[str] = "all"  # all, completed, failed
    scene_ids: Optional[List[str]] = None
    file_types: Optional[List[str]] = None  # images, voiceovers, videos

@router.get("/report/{project_id}")
async def download_generation_report(project_id: str, token: str = Depends(get_token)):
    """Generates and returns a CSV report for all scenes in a project."""
    client = get_db_client(token)

    proj_res = client.table("projects").select("name").eq("id", project_id).execute()
    if not proj_res.data:
        raise HTTPException(status_code=404, detail="Project not found")
    project_name = proj_res.data[0]["name"]

    scenes_res = client.table("scenes").select("*").eq("project_id", project_id).order("id").execute()
    scenes = scenes_res.data or []

    if not scenes:
        raise HTTPException(status_code=400, detail="No scenes found for this project")

    csv_url = await create_generation_report_csv(project_name, scenes)

    return {"download_url": csv_url, "scene_count": len(scenes), "project_name": project_name}

@router.get("/{project_id}")
async def list_files(project_id: str, token: str = Depends(get_token)):
    client = get_db_client(token)
    scenes_response = client.table("scenes").select("id, scene_number, visual_prompt, visual_path, audio_path, merged_path, overall_status").eq("project_id", project_id).execute()
    return scenes_response.data or []

@router.post("/export")
async def export_files(req: ExportFilterRequest, token: str = Depends(get_token)):
    client = get_db_client(token)
    
    # Get project name
    proj_res = client.table("projects").select("name").eq("id", req.project_id).execute()
    project_name = proj_res.data[0]["name"] if proj_res.data else "Project"
    
    # Query scenes
    query = client.table("scenes").select("*").eq("project_id", req.project_id)
    if req.status_filter and req.status_filter.lower() != "all":
        query = query.eq("overall_status", req.status_filter.upper())
    if req.scene_ids:
        query = query.in_("id", req.scene_ids)
        
    scenes_res = query.execute()
    scenes = scenes_res.data or []
    
    if not scenes:
        raise HTTPException(status_code=400, detail="No matching scenes found for export")
        
    zip_url = await create_filtered_export_zip(project_name, scenes, req.file_types)
    return {"download_url": zip_url, "exported_scenes_count": len(scenes)}
