from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
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

@router.get("/{job_id}")
async def get_job_status(job_id: int, token: str = Depends(get_token)):
    client = get_db_client(token)
    res = client.table("generation_jobs").select("*").eq("id", job_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Generation job not found")
    return res.data[0]

@router.post("/{job_id}/pause")
async def pause_job(job_id: int, token: str = Depends(get_token)):
    client = get_db_client(token)
    res = client.table("generation_jobs").select("project_id").eq("id", job_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Generation job not found")
        
    project_id = str(res.data[0]["project_id"])
    pause_generation(project_id)
    return {"status": "paused", "job_id": job_id}

@router.post("/{job_id}/resume")
async def resume_job(job_id: int, token: str = Depends(get_token), user_id: str = Depends(verify_token)):
    client = get_db_client(token)
    res = client.table("generation_jobs").select("project_id").eq("id", job_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Generation job not found")
        
    project_id = str(res.data[0]["project_id"])
    
    # Get pending or failed scenes
    scenes_response = client.table("scenes").select("*").eq("project_id", project_id).in_("overall_status", ["pending", "PENDING", "failed", "FAILED"]).execute()
    scenes = scenes_response.data or []
    
    if not scenes:
        return {"status": "ok", "message": "No pending or failed scenes to resume."}
        
    api_key = await get_active_google_api_key(client)
    await start_generation(project_id, scenes, api_key, user_id=user_id)
    return {"status": "resumed", "job_id": job_id, "scene_count": len(scenes)}

@router.post("/{job_id}/cancel")
async def cancel_job(job_id: int, token: str = Depends(get_token)):
    client = get_db_client(token)
    res = client.table("generation_jobs").select("project_id").eq("id", job_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Generation job not found")
        
    project_id = str(res.data[0]["project_id"])
    cancel_generation(project_id)
    return {"status": "cancelled", "job_id": job_id}

@router.post("/{job_id}/retry")
async def retry_job(job_id: int, token: str = Depends(get_token), user_id: str = Depends(verify_token)):
    client = get_db_client(token)
    res = client.table("generation_jobs").select("project_id").eq("id", job_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Generation job not found")
        
    project_id = str(res.data[0]["project_id"])
    
    # Get failed scenes
    scenes_response = client.table("scenes").select("*").eq("project_id", project_id).in_("overall_status", ["failed", "FAILED"]).execute()
    scenes = scenes_response.data or []
    
    if not scenes:
        return {"status": "ok", "message": "No failed scenes to retry."}
        
    # Reset status to PENDING
    scene_ids = [s["id"] for s in scenes]
    client.table("scenes").update({"overall_status": "PENDING", "error_message": None}).in_("id", scene_ids).execute()
    
    api_key = await get_active_google_api_key(client)
    await start_generation(project_id, scenes, api_key, user_id=user_id)
    return {"status": "retrying", "job_id": job_id, "scene_count": len(scenes)}
