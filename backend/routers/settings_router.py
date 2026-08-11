from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from backend.auth import get_token, verify_token
from backend.database import get_db_client
from backend.services.google_ai_service import test_connection

router = APIRouter()

class ApiKeyRequest(BaseModel):
    api_key: str

@router.get("")
async def get_settings(token: str = Depends(get_token), user_id: str = Depends(verify_token)):
    client = get_db_client(token)
    response = client.table("user_settings").select("*").eq("user_id", user_id).execute()
    if response.data:
        # Mask the key for the frontend
        settings = response.data[0]
        if settings.get("google_api_key_1"):
            settings["has_api_key"] = True
            settings.pop("google_api_key_1")
        return settings
    return {}

@router.post("/api-key")
async def save_api_key(req: ApiKeyRequest, token: str = Depends(get_token), user_id: str = Depends(verify_token)):
    client = get_db_client(token)
    
    # Upsert settings
    response = client.table("user_settings").upsert({
        "user_id": user_id,
        "google_api_key_1": req.api_key
    }).execute()
    
    return {"status": "ok"}

@router.post("/test-api")
async def test_api(req: ApiKeyRequest):
    success = await test_connection(req.api_key)
    if success:
        return {"status": "ok", "message": "Connection successful"}
    raise HTTPException(status_code=400, detail="Connection failed or invalid key")
