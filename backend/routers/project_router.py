from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
from backend.auth import get_token, verify_token
from backend.database import get_db_client

router = APIRouter()

class ProjectCreate(BaseModel):
    name: str
    mode: Optional[str] = "IMAGE_VOICE"
    description: Optional[str] = ""
    scenes: Optional[List[Dict[str, Any]]] = []

class ScenesImportRequest(BaseModel):
    scenes: List[Dict[str, Any]]

@router.get("")
async def list_projects(token: str = Depends(get_token)):
    client = get_db_client(token)
    response = client.table("projects").select("id, name, description, mode, status, total_scenes, completed_scenes, failed_scenes, created_at").order("created_at", desc=True).execute()
    return response.data or []

@router.get("/stats")
async def get_dashboard_stats(token: str = Depends(get_token), user_id: str = Depends(verify_token)):
    client = get_db_client(token)
    
    proj_res = client.table("projects").select("id, status, completed_scenes, failed_scenes, total_scenes").eq("user_id", user_id).execute()
    projects = proj_res.data or []
    
    total_projects = len(projects)
    active_jobs = sum(1 for p in projects if p.get("status") in ["PROCESSING", "generating"])
    completed_scenes = sum(p.get("completed_scenes", 0) for p in projects)
    failed_scenes = sum(p.get("failed_scenes", 0) for p in projects)
    
    profiles_res = client.table("api_profiles").select("id").eq("user_id", user_id).eq("is_active", True).execute()
    api_configured = bool(profiles_res.data)
    
    return {
        "total_projects": total_projects,
        "active_jobs": active_jobs,
        "completed_scenes": completed_scenes,
        "failed_scenes": failed_scenes,
        "api_configured": api_configured
    }

@router.post("")
async def create_project(project: ProjectCreate, token: str = Depends(get_token), user_id: str = Depends(verify_token)):
    client = get_db_client(token)
    
    proj_response = client.table("projects").insert({
        "name": project.name,
        "user_id": user_id,
        "mode": project.mode or "IMAGE_VOICE",
        "description": project.description or "",
        "status": "PENDING",
        "total_scenes": len(project.scenes or [])
    }).execute()
    
    if not proj_response.data:
        raise HTTPException(status_code=500, detail="Failed to create project")
        
    project_id = proj_response.data[0]["id"]
    
    if project.scenes:
        scenes_to_insert = []
        for i, scene in enumerate(project.scenes):
            scenes_to_insert.append({
                "project_id": project_id,
                "user_id": user_id,
                "scene_number": str(scene.get("id") or (i + 1)),
                "visual_prompt": scene.get("visual_prompt", ""),
                "voiceover_script": scene.get("voiceover_script", ""),
                "master_prompt": scene.get("master_prompt"),
                "aspect_ratio": scene.get("aspect_ratio", "16:9"),
                "media_type": scene.get("media_type", "image"),
                "filename": scene.get("filename", f"scene_{i+1:03d}"),
                "style": scene.get("style"),
                "tone": scene.get("tone"),
                "custom_metadata": scene.get("custom_metadata", {}),
                "overall_status": "PENDING",
                "visual_status": "PENDING",
                "voice_status": "PENDING"
            })
            
        client.table("scenes").insert(scenes_to_insert).execute()
        
    return {"id": project_id, "name": project.name}

@router.post("/{project_id}/scenes")
async def import_scenes_to_project(project_id: int, req: ScenesImportRequest, token: str = Depends(get_token), user_id: str = Depends(verify_token)):
    client = get_db_client(token)
    
    proj_res = client.table("projects").select("id").eq("id", project_id).execute()
    if not proj_res.data:
        raise HTTPException(status_code=404, detail="Project not found")
        
    scenes_to_insert = []
    for i, scene in enumerate(req.scenes):
        scenes_to_insert.append({
            "project_id": project_id,
            "user_id": user_id,
            "scene_number": str(scene.get("id") or (i + 1)),
            "visual_prompt": scene.get("visual_prompt", ""),
            "voiceover_script": scene.get("voiceover_script", ""),
            "master_prompt": scene.get("master_prompt"),
            "aspect_ratio": scene.get("aspect_ratio", "16:9"),
            "media_type": scene.get("media_type", "image"),
            "filename": scene.get("filename", f"scene_{i+1:03d}"),
            "style": scene.get("style"),
            "tone": scene.get("tone"),
            "custom_metadata": scene.get("custom_metadata", {}),
            "overall_status": "PENDING",
            "visual_status": "PENDING",
            "voice_status": "PENDING"
        })
        
    if scenes_to_insert:
        client.table("scenes").insert(scenes_to_insert).execute()
        
    # Update total_scenes in projects table
    all_scenes_res = client.table("scenes").select("id").eq("project_id", project_id).execute()
    total_count = len(all_scenes_res.data or [])
    client.table("projects").update({"total_scenes": total_count}).eq("id", project_id).execute()
    
    return {"status": "ok", "imported_count": len(scenes_to_insert), "total_scenes": total_count}

@router.get("/{project_id}")
async def get_project(project_id: int, token: str = Depends(get_token)):
    client = get_db_client(token)
    proj_response = client.table("projects").select("*").eq("id", project_id).execute()
    if not proj_response.data:
        raise HTTPException(status_code=404, detail="Project not found")
        
    project = proj_response.data[0]
    scenes_response = client.table("scenes").select("*").eq("project_id", project_id).order("id").execute()
    project["scenes"] = scenes_response.data or []
    
    return project

@router.delete("/{project_id}")
async def delete_project(project_id: int, token: str = Depends(get_token)):
    client = get_db_client(token)
    client.table("projects").delete().eq("id", project_id).execute()
    return {"success": True}
