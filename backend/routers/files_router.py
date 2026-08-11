import os
from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import List
from backend.auth import get_token, verify_token
from backend.database import get_db_client
from backend.services.export_service import create_export_zip
from backend.config import settings

router = APIRouter()

class ExportRequest(BaseModel):
    project_id: str
    files: List[str]

@router.get("/{project_id}")
async def list_files(project_id: str, token: str = Depends(get_token)):
    client = get_db_client(token)
    scenes_response = client.table("scenes").select("id, image_url, audio_url, video_url").eq("project_id", project_id).execute()
    
    files = []
    for scene in scenes_response.data:
        if scene.get("image_url"):
            files.append(scene["image_url"])
        if scene.get("audio_url"):
            files.append(scene["audio_url"])
        if scene.get("video_url"):
            files.append(scene["video_url"])
            
    return {"files": files}

@router.post("/export")
async def export_files(req: ExportRequest, token: str = Depends(get_token)):
    try:
        zip_path = create_export_zip(req.project_id, req.files)
        zip_filename = os.path.basename(zip_path)
        return {"export_url": f"/api/files/export/{zip_filename}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/export/{export_id}")
async def download_export(export_id: str):
    zip_path = os.path.join(settings.output_dir, export_id)
    if not os.path.exists(zip_path):
        raise HTTPException(status_code=404, detail="Export not found")
        
    return FileResponse(zip_path, filename=export_id)
