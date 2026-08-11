from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional, Union
from backend.auth import get_token, verify_token, decrypt_value
from backend.database import get_db_client
from backend.services.generation_service import start_generation, pause_generation, cancel_generation

router = APIRouter()

class GenerationRequest(BaseModel):
    project_id: str

async def get_active_google_api_key(client) -> str:
    """Fetches and decrypts the active Google API Key for the user."""
    profiles_response = client.table("api_profiles").select("encrypted_credentials").eq("provider", "google").eq("is_active", True).execute()
    if not profiles_response.data or not profiles_response.data[0].get("encrypted_credentials"):
        raise HTTPException(status_code=400, detail="Active Google API Key not configured. Please add one in Settings.")
        
    encrypted_key = profiles_response.data[0]["encrypted_credentials"]
    return decrypt_value(encrypted_key)

@router.post("/start")
async def start_gen(req: GenerationRequest, token: str = Depends(get_token), user_id: str = Depends(verify_token)):
    client = get_db_client(token)
    
    # Get pending or failed scenes
    scenes_response = client.table("scenes").select("*").eq("project_id", req.project_id).in_("overall_status", ["pending", "PENDING", "failed", "FAILED"]).execute()
    scenes = scenes_response.data or []
    
    if not scenes:
        return {"status": "ok", "message": "No pending or failed scenes to process."}
        
    api_key = await get_active_google_api_key(client)
    job_id = await start_generation(req.project_id, scenes, api_key, user_id=user_id)
    
    return {"status": "started", "job_id": job_id, "scene_count": len(scenes)}

@router.get("/active/{project_id}")
async def get_active_job_for_project(project_id: int, token: str = Depends(get_token)):
    client = get_db_client(token)
    res = client.table("generation_jobs").select("*").eq("project_id", project_id).order("id", desc=True).limit(1).execute()
    if not res.data:
        return {"id": None, "status": "IDLE", "total_tasks": 0, "completed_tasks": 0}
    return res.data[0]

@router.get("/{job_id}")
async def get_job_status(job_id: int, token: str = Depends(get_token)):
    client = get_db_client(token)
    res = client.table("generation_jobs").select("*").eq("id", job_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Generation job not found")
    return res.data[0]

@router.post("/{job_id}/pause")
async def pause_job(job_id: Union[int, str], token: str = Depends(get_token)):
    client = get_db_client(token)
    
    # Check if job_id is integer ID or project_id string
    try:
        j_id = int(job_id)
        res = client.table("generation_jobs").select("project_id").eq("id", j_id).execute()
        project_id = str(res.data[0]["project_id"]) if res.data else str(j_id)
    except ValueError:
        project_id = str(job_id)
        
    pause_generation(project_id)
    return {"status": "paused", "project_id": project_id}

@router.post("/{job_id}/resume")
async def resume_job(job_id: Union[int, str], token: str = Depends(get_token), user_id: str = Depends(verify_token)):
    client = get_db_client(token)
    
    try:
        j_id = int(job_id)
        res = client.table("generation_jobs").select("project_id").eq("id", j_id).execute()
        project_id = str(res.data[0]["project_id"]) if res.data else str(j_id)
    except ValueError:
        project_id = str(job_id)
    
    scenes_response = client.table("scenes").select("*").eq("project_id", project_id).in_("overall_status", ["pending", "PENDING", "failed", "FAILED"]).execute()
    scenes = scenes_response.data or []
    
    if not scenes:
        return {"status": "ok", "message": "No pending or failed scenes to resume."}
        
    api_key = await get_active_google_api_key(client)
    res_job_id = await start_generation(project_id, scenes, api_key, user_id=user_id)
    return {"status": "resumed", "job_id": res_job_id, "scene_count": len(scenes)}

@router.post("/{job_id}/cancel")
async def cancel_job(job_id: Union[int, str], token: str = Depends(get_token)):
    client = get_db_client(token)
    try:
        j_id = int(job_id)
        res = client.table("generation_jobs").select("project_id").eq("id", j_id).execute()
        project_id = str(res.data[0]["project_id"]) if res.data else str(j_id)
    except ValueError:
        project_id = str(job_id)
        
    cancel_generation(project_id)
    return {"status": "cancelled", "project_id": project_id}

@router.post("/{job_id}/retry")
async def retry_job(job_id: Union[int, str], token: str = Depends(get_token), user_id: str = Depends(verify_token)):
    client = get_db_client(token)
    try:
        j_id = int(job_id)
        res = client.table("generation_jobs").select("project_id").eq("id", j_id).execute()
        project_id = str(res.data[0]["project_id"]) if res.data else str(j_id)
    except ValueError:
        project_id = str(job_id)
    
    scenes_response = client.table("scenes").select("*").eq("project_id", project_id).in_("overall_status", ["failed", "FAILED"]).execute()
    scenes = scenes_response.data or []
    
    if not scenes:
        return {"status": "ok", "message": "No failed scenes to retry."}
        
    scene_ids = [s["id"] for s in scenes]
    client.table("scenes").update({"overall_status": "PENDING", "error_message": None}).in_("id", scene_ids).execute()
    
    api_key = await get_active_google_api_key(client)
    res_job_id = await start_generation(project_id, scenes, api_key, user_id=user_id)
    return {"status": "retrying", "job_id": res_job_id, "scene_count": len(scenes)}
