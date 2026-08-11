from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from backend.auth import get_token, verify_token
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
        
    # Get API key from settings
    settings_response = client.table("user_settings").select("google_api_key_1").execute()
    if not settings_response.data or not settings_response.data[0].get("google_api_key_1"):
        raise HTTPException(status_code=400, detail="Google API Key not configured")
        
    api_key = settings_response.data[0]["google_api_key_1"]
    
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
