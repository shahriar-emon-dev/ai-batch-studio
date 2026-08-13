"""Settings, API profiles and connection testing (proposal §9–§13, §47)."""

import datetime
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.auth import decrypt_value, encrypt_value, get_token, verify_token
from backend.config import settings as app_settings
from backend.database import get_db_client
from backend.services.api_profile_service import mask_key
from backend.services.google_ai_service import list_voices, test_connection
from backend.services.settings_service import load_user_settings, save_user_settings

logger = logging.getLogger(__name__)
router = APIRouter()

# Never leaves the server (§11, §47).
PROFILE_SECRET_COLUMNS = ("encrypted_credentials", "api_key", "credentials")

# Shape the frontend relies on. Anything absent from an older database is filled
# with a default rather than 500-ing the whole page.
PROFILE_PUBLIC_DEFAULTS = {
    "provider": "google",
    "profile_name": "",
    "is_active": True,
    "connection_status": "untested",
    "last_tested": None,
    "test_result": None,
    "key_hint": None,
    "request_count": 0,
    "success_count": 0,
    "failure_count": 0,
    "last_success_at": None,
    "last_error": None,
    "last_error_at": None,
    "unavailable_until": None,
    "priority": 0,
    "created_at": None,
}


def _public_profile(row: Dict[str, Any]) -> Dict[str, Any]:
    """Strip credentials and normalize the row to the documented shape."""
    safe = {key: value for key, value in row.items() if key not in PROFILE_SECRET_COLUMNS}
    return {**PROFILE_PUBLIC_DEFAULTS, **safe}


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class ApiProfileCreate(BaseModel):
    profile_name: str
    provider: str = "google"
    api_key: str
    is_active: bool = True
    priority: int = 0


class ApiProfileUpdate(BaseModel):
    profile_name: Optional[str] = None
    api_key: Optional[str] = None
    is_active: Optional[bool] = None
    priority: Optional[int] = None


class UserSettingsUpdate(BaseModel):
    default_generation_mode: Optional[str] = None
    default_aspect_ratio: Optional[str] = None
    default_voice: Optional[str] = None
    default_language: Optional[str] = None
    default_concurrency: Optional[int] = None
    default_retry_count: Optional[int] = None
    default_speech_speed: Optional[float] = None
    default_negative_prompt: Optional[str] = None
    merge_enabled: Optional[bool] = None
    video_generation_enabled: Optional[bool] = None


class ApiKeyRequest(BaseModel):
    google_api_key: Optional[str] = None
    api_key: Optional[str] = None
    profile_name: Optional[str] = None


def _profiles_for(client, user_id: str) -> List[Dict[str, Any]]:
    rows = (
        client.table("api_profiles")
        .select("*")
        .eq("user_id", user_id)
        .order("id", desc=True)
        .execute()
        .data
        or []
    )
    profiles = [_public_profile(row) for row in rows]
    # Ordering is done here so the query cannot fail on a column an older
    # database does not have yet.
    profiles.sort(key=lambda p: (-(p.get("priority") or 0), -(p.get("id") or 0)))
    return profiles


@router.get("")
async def get_settings(token: str = Depends(get_token), user_id: str = Depends(verify_token)):
    """Account settings, generation defaults, profiles and storage info (§9)."""
    client = get_db_client(token)
    profiles = _profiles_for(client, user_id)

    return {
        "settings": load_user_settings(client, user_id),
        "profiles": profiles,
        "has_api_key": any(p.get("is_active") for p in profiles if p.get("provider") == "google"),
        "storage": {
            "media_root": "/output",
            "folders": ["images", "audio", "videos", "merged"],
        },
        "models": {
            "image": app_settings.image_model,
            "video": app_settings.video_model,
            "tts": app_settings.tts_model,
            "video_generation_enabled": app_settings.video_generation_enabled,
        },
    }


@router.put("")
async def update_settings(
    req: UserSettingsUpdate, token: str = Depends(get_token), user_id: str = Depends(verify_token)
):
    """Persist generation defaults (§9, §53)."""
    client = get_db_client(token)
    updated = save_user_settings(client, user_id, req.model_dump(exclude_none=True))
    return {"status": "ok", "settings": updated}


@router.get("/voices")
async def get_voices(
    language: str = "", token: str = Depends(get_token), user_id: str = Depends(verify_token)
):
    """Real voice list from Google, using the user's first usable profile."""
    client = get_db_client(token)
    profiles = (
        client.table("api_profiles")
        .select("encrypted_credentials")
        .eq("user_id", user_id)
        .eq("provider", "google")
        .eq("is_active", True)
        .execute()
        .data
        or []
    )
    if not profiles:
        return {"voices": [], "message": "Add an active Google AI profile to load voices."}

    raw_key = decrypt_value(profiles[0]["encrypted_credentials"])
    try:
        voices = await list_voices(raw_key, language)
    except Exception as exc:
        logger.info("Voice list unavailable: %s", exc)
        return {"voices": [], "message": f"Voice list unavailable: {exc}"}

    if not voices:
        return {
            "voices": [],
            "message": "Cloud Text-to-Speech is not enabled for this key; the Gemini TTS fallback will be used.",
        }
    return {"voices": voices}


@router.get("/api-profiles")
async def list_api_profiles(token: str = Depends(get_token), user_id: str = Depends(verify_token)):
    return _profiles_for(client=get_db_client(token), user_id=user_id)


@router.post("/api-profiles")
async def create_api_profile(
    req: ApiProfileCreate, token: str = Depends(get_token), user_id: str = Depends(verify_token)
):
    """Store the key encrypted; only the masked hint ever leaves the server (§11)."""
    raw_key = (req.api_key or "").strip()
    if not raw_key:
        raise HTTPException(status_code=400, detail="API key is required")
    if not (req.profile_name or "").strip():
        raise HTTPException(status_code=400, detail="Profile name is required")

    client = get_db_client(token)
    result = (
        client.table("api_profiles")
        .insert(
            {
                "user_id": user_id,
                "provider": req.provider or "google",
                "profile_name": req.profile_name.strip(),
                "encrypted_credentials": encrypt_value(raw_key),
                "key_hint": mask_key(raw_key),
                "is_active": req.is_active,
                "priority": req.priority,
                "connection_status": "untested",
            }
        )
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=500, detail="Failed to create API profile")

    created = result.data[0]
    return {
        "id": created["id"],
        "profile_name": created["profile_name"],
        "provider": created["provider"],
        "is_active": created["is_active"],
        "key_hint": created.get("key_hint"),
    }


@router.patch("/api-profiles/{profile_id}")
async def update_api_profile(
    profile_id: int,
    req: ApiProfileUpdate,
    token: str = Depends(get_token),
    user_id: str = Depends(verify_token),
):
    client = get_db_client(token)
    updates: Dict[str, Any] = {}

    if req.profile_name is not None:
        updates["profile_name"] = req.profile_name
    if req.is_active is not None:
        updates["is_active"] = req.is_active
    if req.priority is not None:
        updates["priority"] = req.priority
    if req.api_key:
        updates["encrypted_credentials"] = encrypt_value(req.api_key.strip())
        updates["key_hint"] = mask_key(req.api_key.strip())
        # A new key deserves a clean slate.
        updates["connection_status"] = "untested"
        updates["unavailable_until"] = None
        updates["last_error"] = None

    if not updates:
        return {"status": "no_change"}

    updates["updated_at"] = _now_iso()
    result = (
        client.table("api_profiles").update(updates).eq("id", profile_id).eq("user_id", user_id).execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="API profile not found")
    return {"status": "ok", "id": result.data[0]["id"]}


@router.delete("/api-profiles/{profile_id}")
async def delete_api_profile(
    profile_id: int, token: str = Depends(get_token), user_id: str = Depends(verify_token)
):
    client = get_db_client(token)
    result = client.table("api_profiles").delete().eq("id", profile_id).eq("user_id", user_id).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="API profile not found")
    return {"status": "deleted", "id": profile_id}


@router.post("/api-profiles/{profile_id}/test")
async def test_api_profile(
    profile_id: int, token: str = Depends(get_token), user_id: str = Depends(verify_token)
):
    """Make a real request and report exactly what the provider returned (§12)."""
    client = get_db_client(token)
    result = (
        client.table("api_profiles")
        .select("encrypted_credentials")
        .eq("id", profile_id)
        .eq("user_id", user_id)
        .execute()
    )
    if not result.data or not result.data[0].get("encrypted_credentials"):
        raise HTTPException(status_code=404, detail="API profile not found")

    outcome = await test_connection(decrypt_value(result.data[0]["encrypted_credentials"]))

    updates: Dict[str, Any] = {
        "last_tested": _now_iso(),
        "test_result": outcome["status"],
        "connection_status": "active" if outcome["ok"] else outcome["status"].lower(),
    }
    if outcome["ok"]:
        updates["last_success_at"] = _now_iso()
        updates["last_error"] = None
        updates["unavailable_until"] = None
    else:
        updates["last_error"] = outcome["message"][:500]
        updates["last_error_at"] = _now_iso()

    client.table("api_profiles").update(updates).eq("id", profile_id).execute()

    if outcome["ok"]:
        return {"status": "ok", **outcome}
    # 200 with ok=false keeps the exact provider reason visible in the UI.
    return {"status": "failed", **outcome}


@router.post("/test-api")
async def test_api_key(
    req: ApiKeyRequest, token: str = Depends(get_token), user_id: str = Depends(verify_token)
):
    """Test a key before saving it. Requires auth so the endpoint is not an open proxy."""
    raw_key = (req.google_api_key or req.api_key or "").strip()
    if not raw_key:
        raise HTTPException(status_code=400, detail="Missing API key to test")

    outcome = await test_connection(raw_key)
    return {"status": "ok" if outcome["ok"] else "failed", **outcome}


@router.post("/api-key")
@router.put("/api-key")
async def save_api_key(
    req: ApiKeyRequest, token: str = Depends(get_token), user_id: str = Depends(verify_token)
):
    """Single-key convenience endpoint kept for the simplified settings view."""
    raw_key = (req.google_api_key or req.api_key or "").strip()
    if not raw_key:
        raise HTTPException(status_code=400, detail="Missing API key in request body")

    client = get_db_client(token)
    existing = (
        client.table("api_profiles")
        .select("id")
        .eq("user_id", user_id)
        .eq("provider", "google")
        .order("id")
        .limit(1)
        .execute()
    )

    payload = {
        "encrypted_credentials": encrypt_value(raw_key),
        "key_hint": mask_key(raw_key),
        "is_active": True,
        "connection_status": "untested",
        "unavailable_until": None,
        "updated_at": _now_iso(),
    }

    if existing.data:
        client.table("api_profiles").update(payload).eq("id", existing.data[0]["id"]).execute()
        profile_id = existing.data[0]["id"]
    else:
        payload.update(
            {
                "user_id": user_id,
                "provider": "google",
                "profile_name": req.profile_name or "Default Google AI Profile",
            }
        )
        created = client.table("api_profiles").insert(payload).execute()
        profile_id = created.data[0]["id"] if created.data else None

    # The raw key is never echoed back (§11, §47).
    return {"status": "ok", "profile_id": profile_id, "key_hint": mask_key(raw_key)}
