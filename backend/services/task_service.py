"""Generation task planning and state machine (proposal §24, §32, §33, §35).

One row in `generation_tasks` per (scene, asset type). The row carries the
prompt actually sent, the API profile used, attempt count, timings, error and
storage reference, which is what makes a run auditable (§58).
"""

import datetime
import logging
from typing import Any, Dict, List, Optional

from backend.config import settings
from backend.database import get_admin_client
from backend.services import prompt_service

logger = logging.getLogger(__name__)

TASK_IMAGE = "image"
TASK_VOICEOVER = "voiceover"
TASK_VIDEO = "video"
TASK_MERGE = "merge"

# §33 — the full status vocabulary
STATUS_PENDING = "PENDING"
STATUS_QUEUED = "QUEUED"
STATUS_PROCESSING = "PROCESSING"
STATUS_COMPLETED = "COMPLETED"
STATUS_FAILED = "FAILED"
STATUS_RETRYING = "RETRYING"
STATUS_CANCELLED = "CANCELLED"
STATUS_SKIPPED = "SKIPPED"
STATUS_UNSUPPORTED = "UNSUPPORTED"

TERMINAL_STATUSES = {STATUS_COMPLETED, STATUS_CANCELLED, STATUS_SKIPPED, STATUS_UNSUPPORTED}
ACTIVE_STATUSES = {STATUS_PROCESSING, STATUS_RETRYING}
OPEN_STATUSES = {STATUS_PENDING, STATUS_QUEUED, STATUS_FAILED, STATUS_RETRYING}


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _has(value: Any) -> bool:
    return bool(str(value or "").strip())


def plan_tasks(scene: Dict[str, Any], defaults: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Decide which assets this scene needs (§24 media type detection).

    An explicit `media_type` wins; otherwise the requirement is inferred from
    which prompt columns actually carry content.
    """
    defaults = defaults or {}
    media_type = str(scene.get("media_type") or "").strip().lower().replace("-", "_")

    has_visual = _has(scene.get("visual_prompt"))
    has_script = _has(scene.get("voiceover_script"))
    has_video = _has(scene.get("video_prompt"))

    wants_image = wants_voice = wants_video = False

    if media_type in ("image", "image_only", "images", "visual"):
        wants_image = True
    elif media_type in ("voice", "voiceover", "audio", "narration", "tts"):
        wants_voice = True
    elif media_type in ("video", "video_only"):
        wants_video = True
    elif media_type in ("image_voice", "image_and_voice", "both"):
        wants_image, wants_voice = True, True
    elif media_type in ("video_voice", "video_and_voice"):
        wants_video, wants_voice = True, True
    elif media_type in ("all", "image_video_voice"):
        wants_image = wants_voice = wants_video = True
    else:
        wants_image = has_visual
        wants_voice = has_script
        wants_video = has_video

    # Never plan a task with nothing to generate from.
    wants_image = wants_image and (has_visual or _has(scene.get("master_prompt")))
    wants_voice = wants_voice and has_script
    wants_video = wants_video and (has_video or has_visual or _has(scene.get("master_prompt")))

    tasks: List[Dict[str, Any]] = []

    if wants_image:
        tasks.append({"task_type": TASK_IMAGE, "prompt": prompt_service.compose_image_prompt(scene)})

    if wants_voice:
        script, _ = prompt_service.compose_voice_request(scene, defaults)
        tasks.append({"task_type": TASK_VOICEOVER, "prompt": script})

    if wants_video:
        tasks.append({"task_type": TASK_VIDEO, "prompt": prompt_service.compose_video_prompt(scene)})

    merge_enabled = defaults.get("merge_enabled")
    if merge_enabled is None:
        merge_enabled = settings.merge_enabled
    if merge_enabled and wants_image and wants_voice:
        tasks.append({"task_type": TASK_MERGE, "prompt": ""})

    return tasks


def upsert_task(
    user_id: Optional[str],
    project_id: Any,
    job_id: Optional[int],
    scene: Dict[str, Any],
    task_type: str,
    prompt: str,
) -> Optional[Dict[str, Any]]:
    """Create or reopen the task row for this (scene, asset type).

    Idempotent: an existing COMPLETED task is returned untouched so a resumed
    job does not regenerate assets that already exist (§32).
    """
    client = get_admin_client()
    if not client:
        return None

    scene_id = scene.get("id")
    try:
        existing = (
            client.table("generation_tasks")
            .select("*")
            .eq("scene_id", scene_id)
            .eq("task_type", task_type)
            .limit(1)
            .execute()
            .data
        )
    except Exception as exc:
        logger.error("Task lookup failed for scene %s/%s: %s", scene_id, task_type, exc)
        return None

    payload = {
        "user_id": user_id,
        "project_id": int(project_id) if str(project_id).isdigit() else None,
        "job_id": job_id,
        "scene_id": scene_id,
        "scene_number": str(scene.get("scene_number") or scene_id),
        "task_type": task_type,
        "prompt": (prompt or "")[:5000],
        "max_attempts": settings.retry_max_attempts,
        "updated_at": _now_iso(),
    }

    try:
        if existing:
            current = existing[0]
            if current.get("status") == STATUS_COMPLETED:
                return current
            payload["status"] = STATUS_QUEUED
            payload["error_message"] = None
            payload["error_category"] = None
            # Requeuing is a fresh run, so the attempt budget starts over —
            # otherwise the monitor ends up showing "4 / 3 attempts".
            payload["attempt_count"] = 0
            result = (
                client.table("generation_tasks")
                .update(payload)
                .eq("id", current["id"])
                .execute()
            )
            return (result.data or [current])[0]

        payload["status"] = STATUS_QUEUED
        payload["attempt_count"] = 0
        payload["created_at"] = _now_iso()
        result = client.table("generation_tasks").insert(payload).execute()
        return (result.data or [None])[0]
    except Exception as exc:
        logger.error("Task upsert failed for scene %s/%s: %s", scene_id, task_type, exc)
        return None


def update_task(task_id: Optional[int], **fields: Any) -> None:
    """Patch a task row. Silently ignored when there is no task/client."""
    client = get_admin_client()
    if not client or not task_id:
        return
    fields["updated_at"] = _now_iso()
    try:
        client.table("generation_tasks").update(fields).eq("id", task_id).execute()
    except Exception as exc:
        logger.error("Task %s update failed: %s", task_id, exc)


def mark_processing(task_id: Optional[int], attempt: int, api_profile_id: Optional[int] = None) -> None:
    update_task(
        task_id,
        status=STATUS_PROCESSING,
        attempt_count=attempt,
        api_profile_id=api_profile_id,
        started_at=_now_iso(),
        error_message=None,
        error_category=None,
    )


def mark_completed(task_id: Optional[int], storage_path: str, api_profile_id: Optional[int] = None) -> None:
    update_task(
        task_id,
        status=STATUS_COMPLETED,
        storage_path=storage_path,
        api_profile_id=api_profile_id,
        completed_at=_now_iso(),
        error_message=None,
        error_category=None,
    )


def mark_failed(task_id: Optional[int], category: str, message: str, attempt: int) -> None:
    update_task(
        task_id,
        status=STATUS_FAILED,
        error_category=category,
        error_message=message[:2000],
        attempt_count=attempt,
        completed_at=_now_iso(),
    )


def mark_unsupported(task_id: Optional[int], message: str) -> None:
    update_task(
        task_id,
        status=STATUS_UNSUPPORTED,
        error_category="UNSUPPORTED",
        error_message=message[:2000],
        completed_at=_now_iso(),
    )


def mark_status(task_id: Optional[int], status: str, message: str = "") -> None:
    fields: Dict[str, Any] = {"status": status}
    if message:
        fields["error_message"] = message[:2000]
    update_task(task_id, **fields)


def cancel_open_tasks(project_id: Any) -> int:
    """Move every not-yet-finished task of a project to CANCELLED (§33)."""
    client = get_admin_client()
    if not client:
        return 0
    try:
        result = (
            client.table("generation_tasks")
            .update({"status": STATUS_CANCELLED, "updated_at": _now_iso()})
            .eq("project_id", int(project_id))
            .in_("status", [STATUS_PENDING, STATUS_QUEUED, STATUS_PROCESSING, STATUS_RETRYING])
            .execute()
        )
        return len(result.data or [])
    except Exception as exc:
        logger.error("Cancelling tasks for project %s failed: %s", project_id, exc)
        return 0


def compute_progress(client, project_id: Any, job_id: Optional[int] = None) -> Dict[str, Any]:
    """Aggregate real task rows into the numbers the UI shows (§35, §36)."""
    query = client.table("generation_tasks").select(
        "id, task_type, status, scene_id, scene_number, error_message"
    )
    if job_id:
        query = query.eq("job_id", job_id)
    else:
        query = query.eq("project_id", int(project_id))

    try:
        tasks = query.execute().data or []
    except Exception as exc:
        logger.error("Progress query failed for project %s: %s", project_id, exc)
        tasks = []

    def counts_for(task_type: Optional[str] = None) -> Dict[str, int]:
        subset = [t for t in tasks if task_type is None or t.get("task_type") == task_type]
        return {
            "total": len(subset),
            "completed": sum(1 for t in subset if t.get("status") == STATUS_COMPLETED),
            "failed": sum(1 for t in subset if t.get("status") == STATUS_FAILED),
            "processing": sum(1 for t in subset if t.get("status") in ACTIVE_STATUSES),
            "pending": sum(1 for t in subset if t.get("status") in (STATUS_PENDING, STATUS_QUEUED)),
            "cancelled": sum(1 for t in subset if t.get("status") == STATUS_CANCELLED),
            "unsupported": sum(1 for t in subset if t.get("status") == STATUS_UNSUPPORTED),
            "skipped": sum(1 for t in subset if t.get("status") == STATUS_SKIPPED),
        }

    overall = counts_for()
    finished = overall["completed"] + overall["unsupported"] + overall["skipped"] + overall["cancelled"]
    percent = round((finished / overall["total"]) * 100) if overall["total"] else 0

    processing_tasks = [t for t in tasks if t.get("status") in ACTIVE_STATUSES]

    return {
        "overall": overall,
        "percent": percent,
        "images": counts_for(TASK_IMAGE),
        "voiceovers": counts_for(TASK_VOICEOVER),
        "videos": counts_for(TASK_VIDEO),
        "merges": counts_for(TASK_MERGE),
        "currently_processing": [
            {
                "scene_number": t.get("scene_number"),
                "scene_id": t.get("scene_id"),
                "task_type": t.get("task_type"),
            }
            for t in processing_tasks
        ],
        "recent_failures": [
            {
                "scene_number": t.get("scene_number"),
                "task_type": t.get("task_type"),
                "error": t.get("error_message"),
            }
            for t in tasks
            if t.get("status") == STATUS_FAILED
        ][:20],
    }
