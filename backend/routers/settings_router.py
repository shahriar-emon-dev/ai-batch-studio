from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from backend.auth import get_token, verify_token, encrypt_value, decrypt_value
from backend.database import get_db_client
from backend.services.google_ai_service import test_connection

router = APIRouter()

class ApiKeyRequest(BaseModel):
    api_key: str

@router.get("")
async def get_settings(token: str = Depends(get_token), user_id: str = Depends(verify_token)):
    client = get_db_client(token)
    
    # 1. Fetch user settings
    settings_response = client.table("user_settings").select("*").eq("user_id", user_id).execute()
    settings = settings_response.data[0] if settings_response.data else {}
    
    # 2. Fetch API profiles
    profiles_response = client.table("api_profiles").select("id, provider, profile_name, is_active, last_tested").eq("user_id", user_id).execute()
    
    # Check if there's an active Google profile
    has_api_key = False
    for profile in profiles_response.data:
        if profile["provider"] == "google" and profile["is_active"]:
            has_api_key = True
            break
            
    settings["has_api_key"] = has_api_key
    settings["profiles"] = profiles_response.data
    return settings

@router.post("/api-key")
async def save_api_key(req: ApiKeyRequest, token: str = Depends(get_token), user_id: str = Depends(verify_token)):
    client = get_db_client(token)
    
    encrypted_key = encrypt_value(req.api_key)
    
    # Check if profile already exists
    existing = client.table("api_profiles").select("id").eq("user_id", user_id).eq("provider", "google").execute()
    
    if existing.data:
        # Update
        client.table("api_profiles").update({
            "encrypted_credentials": encrypted_key,
            "is_active": True
        }).eq("id", existing.data[0]["id"]).execute()
    else:
        # Insert
        client.table("api_profiles").insert({
            "user_id": user_id,
            "provider": "google",
            "profile_name": "Default Google API",
            "encrypted_credentials": encrypted_key,
            "is_active": True
        }).execute()
    
    return {"status": "ok"}

@router.post("/test-api")
async def test_api(req: ApiKeyRequest):
    success = await test_connection(req.api_key)
    if success:
        return {"status": "ok", "message": "Connection successful"}
    raise HTTPException(status_code=400, detail="Connection failed or invalid key")
