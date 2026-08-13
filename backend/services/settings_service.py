"""User settings + generation defaults (proposal §9, §53).

Defaults are read from the database per user; the .env values only supply the
fallback when a user has never saved preferences.
"""

import logging
from typing import Any, Dict, Optional

from backend.config import settings as app_settings

logger = logging.getLogger(__name__)

DEFAULT_SETTINGS: Dict[str, Any] = {
    "default_generation_mode": "IMAGE_VOICE",
    "default_aspect_ratio": "16:9",
    "default_voice": "",
    "default_language": "en-US",
    "default_concurrency": None,  # resolved from app config below
    "default_retry_count": None,
    "default_speech_speed": 1.0,
    "default_negative_prompt": "",
    "merge_enabled": True,
    "video_generation_enabled": False,
}

EDITABLE_FIELDS = tuple(DEFAULT_SETTINGS.keys())


def load_user_settings(client, user_id: str) -> Dict[str, Any]:
    """Return the user's saved settings merged over the system defaults."""
    resolved = dict(DEFAULT_SETTINGS)
    resolved["default_concurrency"] = app_settings.default_concurrency
    resolved["default_retry_count"] = app_settings.retry_max_attempts
    resolved["default_language"] = app_settings.default_voice_language
    resolved["merge_enabled"] = app_settings.merge_enabled
    resolved["video_generation_enabled"] = app_settings.video_generation_enabled

    try:
        rows = client.table("user_settings").select("*").eq("user_id", user_id).limit(1).execute().data
    except Exception as exc:
        logger.debug("Could not load user settings: %s", exc)
        rows = None

    if rows:
        for key, value in rows[0].items():
            if key in DEFAULT_SETTINGS and value is not None:
                resolved[key] = value
        resolved["id"] = rows[0].get("id")

    return resolved


def save_user_settings(client, user_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
    """Upsert the editable subset of settings for this user."""
    payload = {key: value for key, value in updates.items() if key in EDITABLE_FIELDS and value is not None}
    if not payload:
        return load_user_settings(client, user_id)

    payload["user_id"] = user_id
    client.table("user_settings").upsert(payload, on_conflict="user_id").execute()
    return load_user_settings(client, user_id)


def generation_defaults(client, user_id: str) -> Dict[str, Any]:
    """The subset the generation pipeline consumes."""
    user = load_user_settings(client, user_id)
    return {
        "default_aspect_ratio": user.get("default_aspect_ratio"),
        "default_voice": user.get("default_voice"),
        "default_language": user.get("default_language"),
        "default_speech_speed": user.get("default_speech_speed"),
        "default_negative_prompt": user.get("default_negative_prompt"),
        "merge_enabled": user.get("merge_enabled"),
        "concurrency": user.get("default_concurrency"),
    }
