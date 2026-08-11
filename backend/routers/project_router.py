from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List, Dict, Any
from backend.auth import get_token, verify_token
from backend.database import get_db_client

router = APIRouter()

class ProjectCreate(BaseModel):
    name: str
    scenes: List[Dict[str, Any]]

@router.get("")
async def list_projects(token: str = Depends(get_token)):
    client = get_db_client(token)
    response = client.table("projects").select("id, name, created_at, status").order("created_at", desc=True).execute()
    return response.data

@router.post("")
async def create_project(project: ProjectCreate, token: str = Depends(get_token), user_id: str = Depends(verify_token)):
    client = get_db_client(token)
    
    # 1. Create project
    proj_response = client.table("projects").insert({
        "name": project.name,
        "user_id": user_id,
        "status": "idle"
    }).execute()
    
    if not proj_response.data:
        raise HTTPException(status_code=500, detail="Failed to create project")
        
    project_id = proj_response.data[0]["id"]
    
    # 2. Insert scenes
    scenes_to_insert = []
    for i, scene in enumerate(project.scenes):
        scenes_to_insert.append({
            "project_id": project_id,
            "scene_order": i,
            "visual_prompt": scene.get("visual_prompt", ""),
            "voiceover_script": scene.get("voiceover_script", ""),
            "status": "pending"
        })
        
    if scenes_to_insert:
        client.table("scenes").insert(scenes_to_insert).execute()
        
    return {"id": project_id, "name": project.name}

@router.get("/{project_id}")
async def get_project(project_id: str, token: str = Depends(get_token)):
    client = get_db_client(token)
    # Get project
    proj_response = client.table("projects").select("*").eq("id", project_id).execute()
    if not proj_response.data:
        raise HTTPException(status_code=404, detail="Project not found")
        
    project = proj_response.data[0]
    
    # Get scenes
    scenes_response = client.table("scenes").select("*").eq("project_id", project_id).order("scene_order").execute()
    project["scenes"] = scenes_response.data
    
    return project

@router.delete("/{project_id}")
async def delete_project(project_id: str, token: str = Depends(get_token)):
    client = get_db_client(token)
    client.table("projects").delete().eq("id", project_id).execute()
    return {"success": True}
