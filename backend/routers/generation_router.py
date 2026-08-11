from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from backend.auth import get_token, verify_token, decrypt_value
from backend.database import get_db_client
from backend.services.generation_service import start_generation, cancel_generation

router = APIRouter()

class GenerationRequest(BaseModel):
    project_id: str

@router.post("/start")
async def start_gen(req: GenerationRequest, token: str = Depends(get_token)):
    client = get_db_client(token)
    
    # Get project and scenes
    scenes_response = client.table("scenes").select("*").eq("project_id", req.project_id).in_("status", ["pending", "failed"]).execute()
    scenes = scenes_response.data
    
    if not scenes:
        return {"status": "ok", "message": "No pending or failed scenes"}
        
    # Get API key from profiles
    profiles_response = client.table("api_profiles").select("encrypted_credentials").eq("provider", "google").eq("is_active", True).execute()
    if not profiles_response.data or not profiles_response.data[0].get("encrypted_credentials"):
        raise HTTPException(status_code=400, detail="Google API Key not configured")
        
    encrypted_key = profiles_response.data[0]["encrypted_credentials"]
    api_key = decrypt_value(encrypted_key)
    
    # Update project status
    client.table("projects").update({"status": "generating"}).eq("id", req.project_id).execute()
    
    # Start generation in background
    await start_generation(req.project_id, scenes, api_key)
    return {"status": "started"}

@router.post("/cancel")
async def cancel_gen(req: GenerationRequest, token: str = Depends(get_token)):
    client = get_db_client(token)
    
    cancel_generation(req.project_id)
    client.table("projects").update({"status": "idle"}).eq("id", req.project_id).execute()
    return {"status": "cancelled"}
