from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
from backend.auth import get_token, verify_token, encrypt_value, decrypt_value
from backend.database import get_db_client
from backend.services.google_ai_service import test_connection

router = APIRouter()

class ApiKeyRequest(BaseModel):
    api_key: str

class ApiProfileCreate(BaseModel):
    profile_name: str
    provider: str = "google"
    api_key: str
    is_active: bool = True

class ApiProfileUpdate(BaseModel):
    profile_name: Optional[str] = None
    api_key: Optional[str] = None
    is_active: Optional[bool] = None

@router.get("")
async def get_settings(token: str = Depends(get_token), user_id: str = Depends(verify_token)):
    client = get_db_client(token)
    
    # 1. Fetch user settings
    settings_response = client.table("user_settings").select("*").eq("user_id", user_id).execute()
    settings = settings_response.data[0] if settings_response.data else {}
    
    # 2. Fetch API profiles
    profiles_response = client.table("api_profiles").select("id, provider, profile_name, is_active, last_tested, test_result").eq("user_id", user_id).execute()
    
    has_api_key = any(p.get("provider") == "google" and p.get("is_active") for p in profiles_response.data)
            
    settings["has_api_key"] = has_api_key
    settings["profiles"] = profiles_response.data
    return settings

@router.get("/api-profiles")
async def list_api_profiles(token: str = Depends(get_token), user_id: str = Depends(verify_token)):
    client = get_db_client(token)
    profiles_response = client.table("api_profiles").select("id, provider, profile_name, is_active, last_tested, test_result, created_at").eq("user_id", user_id).order("created_at", desc=True).execute()
    return profiles_response.data

@router.post("/api-profiles")
async def create_api_profile(req: ApiProfileCreate, token: str = Depends(get_token), user_id: str = Depends(verify_token)):
    client = get_db_client(token)
    encrypted_key = encrypt_value(req.api_key)
    
    res = client.table("api_profiles").insert({
        "user_id": user_id,
        "provider": req.provider,
        "profile_name": req.profile_name,
        "encrypted_credentials": encrypted_key,
        "is_active": req.is_active
    }).execute()
    
    if not res.data:
        raise HTTPException(status_code=500, detail="Failed to create API profile")
        
    created = res.data[0]
    return {
        "id": created["id"],
        "profile_name": created["profile_name"],
        "provider": created["provider"],
        "is_active": created["is_active"]
    }

@router.patch("/api-profiles/{profile_id}")
async def update_api_profile(profile_id: int, req: ApiProfileUpdate, token: str = Depends(get_token), user_id: str = Depends(verify_token)):
    client = get_db_client(token)
    
    updates = {}
    if req.profile_name is not None:
        updates["profile_name"] = req.profile_name
    if req.is_active is not None:
        updates["is_active"] = req.is_active
    if req.api_key is not None:
        updates["encrypted_credentials"] = encrypt_value(req.api_key)
        
    if not updates:
        return {"status": "no_change"}
        
    res = client.table("api_profiles").update(updates).eq("id", profile_id).eq("user_id", user_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="API profile not found")
        
    return {"status": "ok", "updated": res.data[0]["id"]}

@router.delete("/api-profiles/{profile_id}")
async def delete_api_profile(profile_id: int, token: str = Depends(get_token), user_id: str = Depends(verify_token)):
    client = get_db_client(token)
    client.table("api_profiles").delete().eq("id", profile_id).eq("user_id", user_id).execute()
    return {"status": "deleted"}

@router.post("/api-profiles/{profile_id}/test")
async def test_api_profile(profile_id: int, token: str = Depends(get_token), user_id: str = Depends(verify_token)):
    client = get_db_client(token)
    
    res = client.table("api_profiles").select("encrypted_credentials").eq("id", profile_id).eq("user_id", user_id).execute()
    if not res.data or not res.data[0].get("encrypted_credentials"):
        raise HTTPException(status_code=404, detail="API profile not found")
        
    encrypted_key = res.data[0]["encrypted_credentials"]
    raw_key = decrypt_value(encrypted_key)
    
    success = await test_connection(raw_key)
    test_result = "SUCCESS" if success else "FAILED"
    
    # Update last_tested
    import datetime
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    client.table("api_profiles").update({"last_tested": now_iso, "test_result": test_result}).eq("id", profile_id).execute()
    
    if success:
        return {"status": "ok", "message": "Connection successful"}
    raise HTTPException(status_code=400, detail="API test failed: Invalid credentials or network error")

# Legacy compatibility endpoints
@router.post("/api-key")
async def save_api_key(req: ApiKeyRequest, token: str = Depends(get_token), user_id: str = Depends(verify_token)):
    client = get_db_client(token)
    encrypted_key = encrypt_value(req.api_key)
    
    existing = client.table("api_profiles").select("id").eq("user_id", user_id).eq("provider", "google").execute()
    if existing.data:
        client.table("api_profiles").update({"encrypted_credentials": encrypted_key, "is_active": True}).eq("id", existing.data[0]["id"]).execute()
    else:
        client.table("api_profiles").insert({
            "user_id": user_id,
            "provider": "google",
            "profile_name": "Default Google API Profile",
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
