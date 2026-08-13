"""Structured activity + error logging (proposal §45, §48, §57, §58).

Both writers are best-effort: observability must never break a generation run.
Secrets are never passed in here — callers log messages, not credentials.
"""

import datetime
import logging
from typing import Any, Dict, Optional

from backend.database import get_admin_client

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _as_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def log_activity(
    project_id: Any,
    user_id: Optional[str],
    level: str,
    message: str,
    scene_id: Any = None,
    scene_number: Any = None,
) -> None:
    """Append a human-readable line to the project activity feed."""
    client = get_admin_client()
    if not client or not user_id:
        return
    try:
        client.table("activity_logs").insert(
            {
                "project_id": _as_int(project_id),
                "user_id": user_id,
                "scene_id": _as_int(scene_id),
                "scene_number": str(scene_number) if scene_number is not None else None,
                "level": level,
                "message": message[:2000],
                "timestamp": _now_iso(),
            }
        ).execute()
    except Exception as exc:  # pragma: no cover - telemetry only
        logger.debug("activity log failed: %s", exc)


def log_error(
    user_id: Optional[str],
    project_id: Any,
    error_category: str,
    error_message: str,
    scene_id: Any = None,
    task_id: Any = None,
    api_profile_id: Any = None,
    is_retryable: bool = False,
    attempt_count: int = 1,
) -> None:
    """Record a classified failure so it can be inspected later (§58)."""
    client = get_admin_client()
    if not client or not user_id:
        return
    try:
        client.table("error_logs").insert(
            {
                "user_id": user_id,
                "project_id": _as_int(project_id),
                "scene_id": _as_int(scene_id),
                "task_id": _as_int(task_id),
                "api_profile_id": _as_int(api_profile_id),
                "error_category": error_category,
                "error_message": error_message[:2000],
                "is_retryable": is_retryable,
                "attempt_count": attempt_count,
                "created_at": _now_iso(),
            }
        ).execute()
    except Exception as exc:  # pragma: no cover - telemetry only
        logger.debug("error log failed: %s", exc)


def summarize_error(exc: Exception) -> Dict[str, Any]:
    """Normalize any exception into (category, message, retryable)."""
    return {
        "category": getattr(exc, "category", "PROVIDER_ERROR"),
        "message": str(exc)[:2000] or exc.__class__.__name__,
        "retryable": bool(getattr(exc, "retryable", False)),
    }
