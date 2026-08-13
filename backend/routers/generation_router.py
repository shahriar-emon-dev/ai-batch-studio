"""Generation control + live progress API (proposal §32, §35, §39, §58)."""

import logging
from typing import Any, Dict, List, Optional, Union

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from backend.auth import get_token, verify_token
from backend.database import get_admin_client, get_db_client, is_service_role_configured
from backend.services import task_service
from backend.services.api_profile_service import load_profile_pool
from backend.services.generation_service import (
    active_jobs,
    cancel_generation,
    is_running,
    pause_generation,
    start_generation,
    sync_scene_state,
    update_job_progress,
)
from backend.services.settings_service import generation_defaults

logger = logging.getLogger(__name__)
router = APIRouter()

RESUMABLE_SCENE_STATUSES = ["PENDING", "pending", "FAILED", "failed", "PROCESSING", "PAUSED", "CANCELLED"]


class GenerationRequest(BaseModel):
    project_id: str
    scene_ids: Optional[List[int]] = None
    only_failed: bool = False


def _assert_project_access(client, project_id: Any) -> Dict[str, Any]:
    """RLS already isolates users; this turns a filtered-out row into a 404."""
    result = client.table("projects").select("id, name, user_id").eq("id", project_id).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Project not found")
    return result.data[0]


def _assert_worker_can_write() -> None:
    """A job that cannot persist its results must not appear to start (§56, §61)."""
    if not is_service_role_configured():
        raise HTTPException(
            status_code=503,
            detail=(
                "Server is missing SUPABASE_SERVICE_ROLE_KEY, so the generation worker cannot "
                "save results. Add the service_role key from Supabase → Settings → API to .env "
                "and restart the backend."
            ),
        )


def _load_pool(client, user_id: str):
    pool = load_profile_pool(client, user_id)
    if pool.size == 0:
        raise HTTPException(
            status_code=400,
            detail="No active Google AI API profile configured. Add one in Settings first.",
        )
    if pool.available_count() == 0:
        retry_at = pool.next_retry_at()
        raise HTTPException(
            status_code=429,
            detail=(
                "Every configured API profile is rate limited or out of quota"
                + (f" until {retry_at.isoformat()}" if retry_at else "")
                + ". Add another profile or wait for the cooldown to expire."
            ),
        )
    return pool


def _fetch_scenes(client, project_id: Any, scene_ids=None, only_failed=False) -> List[Dict[str, Any]]:
    query = client.table("scenes").select("*").eq("project_id", project_id)
    if scene_ids:
        query = query.in_("id", scene_ids)
    elif only_failed:
        query = query.in_("overall_status", ["FAILED", "failed"])
    else:
        query = query.in_("overall_status", RESUMABLE_SCENE_STATUSES)
    return query.order("id").execute().data or []


async def resolve_project_id(job_id: Union[int, str], client) -> str:
    """Accept either a job id or a project id (the UI sends whichever it has)."""
    try:
        candidate = int(job_id)
    except (TypeError, ValueError):
        return str(job_id)

    result = client.table("generation_jobs").select("project_id").eq("id", candidate).execute()
    if result.data and result.data[0].get("project_id"):
        return str(result.data[0]["project_id"])
    return str(candidate)


@router.post("/start")
async def start_gen(
    req: GenerationRequest,
    token: str = Depends(get_token),
    user_id: str = Depends(verify_token),
):
    client = get_db_client(token)
    _assert_project_access(client, req.project_id)

    if is_running(req.project_id):
        raise HTTPException(status_code=409, detail="Generation is already running for this project")

    scenes = _fetch_scenes(client, req.project_id, req.scene_ids, req.only_failed)
    if not scenes:
        return {"status": "ok", "message": "No pending or failed scenes to process.", "task_count": 0}

    _assert_worker_can_write()

    pool = _load_pool(client, user_id)
    defaults = generation_defaults(client, user_id)
    result = await start_generation(req.project_id, scenes, pool, user_id=user_id, defaults=defaults)
    return {"status": result["status"].lower(), **result}


@router.get("/progress/{project_id}")
async def get_progress(project_id: int, token: str = Depends(get_token), user_id: str = Depends(verify_token)):
    """Everything the live progress screen needs, computed from task rows (§35, §36)."""
    client = get_db_client(token)
    project = client.table("projects").select("*").eq("id", project_id).execute()
    if not project.data:
        raise HTTPException(status_code=404, detail="Project not found")

    job = (
        client.table("generation_jobs")
        .select("*")
        .eq("project_id", project_id)
        .order("id", desc=True)
        .limit(1)
        .execute()
    )
    job_row = job.data[0] if job.data else None

    # Project-scoped so a refresh always reports the true cumulative state (§38).
    progress = task_service.compute_progress(client, project_id)

    # Scene-level readiness. Tasks only exist once a job has started, so the UI
    # cannot use task counts to decide whether generation *can* start — that
    # would hide the Start button on a project that has never run.
    scene_rows = (
        client.table("scenes").select("overall_status").eq("project_id", project_id).execute().data or []
    )
    resumable = set(RESUMABLE_SCENE_STATUSES)
    scenes_summary = {
        "total": len(scene_rows),
        "pending": sum(1 for s in scene_rows if (s.get("overall_status") or "PENDING") in resumable),
        "completed": sum(1 for s in scene_rows if s.get("overall_status") == "COMPLETED"),
        "failed": sum(1 for s in scene_rows if s.get("overall_status") == "FAILED"),
        "skipped": sum(1 for s in scene_rows if s.get("overall_status") in ("SKIPPED", "CANCELLED")),
    }

    return {
        "project": project.data[0],
        "job": job_row,
        "worker_active": is_running(project_id),
        "status": (job_row or {}).get("status") or project.data[0].get("status") or "IDLE",
        "scenes": scenes_summary,
        "can_start": not is_running(project_id) and scenes_summary["pending"] > 0,
        **progress,
    }


@router.get("/tasks/{project_id}")
async def list_tasks(
    project_id: int,
    status: Optional[str] = None,
    task_type: Optional[str] = None,
    limit: int = Query(500, le=2000),
    token: str = Depends(get_token), user_id: str = Depends(verify_token),
):
    """Task monitor feed — one row per asset (§32)."""
    client = get_db_client(token)
    query = client.table("generation_tasks").select("*").eq("project_id", project_id)
    if status:
        query = query.eq("status", status.upper())
    if task_type:
        query = query.eq("task_type", task_type.lower())
    return query.order("scene_id").limit(limit).execute().data or []


@router.get("/logs/{project_id}")
async def list_activity(
    project_id: int,
    limit: int = Query(100, le=500),
    token: str = Depends(get_token), user_id: str = Depends(verify_token),
):
    client = get_db_client(token)
    rows = (
        client.table("activity_logs")
        .select("*")
        .eq("project_id", project_id)
        .order("id", desc=True)
        .limit(limit)
        .execute()
        .data
        or []
    )
    return list(reversed(rows))


@router.get("/errors/{project_id}")
async def list_errors(
    project_id: int,
    limit: int = Query(100, le=500),
    token: str = Depends(get_token), user_id: str = Depends(verify_token),
):
    client = get_db_client(token)
    return (
        client.table("error_logs")
        .select("*")
        .eq("project_id", project_id)
        .order("id", desc=True)
        .limit(limit)
        .execute()
        .data
        or []
    )


@router.get("/active/{project_id}")
async def get_active_job_for_project(project_id: int, token: str = Depends(get_token), user_id: str = Depends(verify_token)):
    client = get_db_client(token)
    result = (
        client.table("generation_jobs")
        .select("*")
        .eq("project_id", project_id)
        .order("id", desc=True)
        .limit(1)
        .execute()
    )
    if not result.data:
        return {"id": None, "status": "IDLE", "total_tasks": 0, "completed_tasks": 0,
                "worker_active": False}
    return {**result.data[0], "worker_active": is_running(project_id)}


@router.get("/{job_id}")
async def get_job_status(job_id: int, token: str = Depends(get_token), user_id: str = Depends(verify_token)):
    client = get_db_client(token)
    result = client.table("generation_jobs").select("*").eq("id", job_id).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Generation job not found")
    return result.data[0]


@router.post("/{job_id}/pause")
async def pause_job(job_id: Union[int, str], token: str = Depends(get_token), user_id: str = Depends(verify_token)):
    client = get_db_client(token)
    project_id = await resolve_project_id(job_id, client)
    _assert_project_access(client, project_id)
    was_running = await pause_generation(project_id, user_id)
    return {"status": "paused", "project_id": project_id, "was_running": was_running}


@router.post("/{job_id}/resume")
async def resume_job(job_id: Union[int, str], token: str = Depends(get_token), user_id: str = Depends(verify_token)):
    client = get_db_client(token)
    project_id = await resolve_project_id(job_id, client)
    _assert_project_access(client, project_id)

    if is_running(project_id):
        return {"status": "already_running", "project_id": project_id}

    scenes = _fetch_scenes(client, project_id)
    if not scenes:
        return {"status": "ok", "message": "No pending or failed scenes to resume.", "task_count": 0}

    _assert_worker_can_write()

    pool = _load_pool(client, user_id)
    defaults = generation_defaults(client, user_id)
    result = await start_generation(project_id, scenes, pool, user_id=user_id, defaults=defaults)
    return {"status": "resumed", "project_id": project_id, **result}


@router.post("/{job_id}/cancel")
async def cancel_job(job_id: Union[int, str], token: str = Depends(get_token), user_id: str = Depends(verify_token)):
    client = get_db_client(token)
    project_id = await resolve_project_id(job_id, client)
    _assert_project_access(client, project_id)
    was_running = await cancel_generation(project_id, user_id)

    for scene in client.table("scenes").select("id").eq("project_id", project_id).execute().data or []:
        sync_scene_state(scene["id"])
    return {"status": "cancelled", "project_id": project_id, "was_running": was_running}


@router.post("/{job_id}/retry")
async def retry_job(job_id: Union[int, str], token: str = Depends(get_token), user_id: str = Depends(verify_token)):
    """Requeue every failed task in the project (§39, §51)."""
    client = get_db_client(token)
    project_id = await resolve_project_id(job_id, client)
    _assert_project_access(client, project_id)

    admin = get_admin_client()
    failed = (
        client.table("generation_tasks")
        .select("id, scene_id")
        .eq("project_id", project_id)
        .in_("status", [task_service.STATUS_FAILED, task_service.STATUS_CANCELLED])
        .execute()
        .data
        or []
    )
    if not failed and admin:
        # Nothing task-level to retry: fall back to scenes marked FAILED.
        failed_scenes = client.table("scenes").select("id").eq("project_id", project_id).in_(
            "overall_status", ["FAILED", "failed"]
        ).execute().data or []
        if not failed_scenes:
            return {"status": "ok", "message": "No failed work to retry.", "task_count": 0}

    if failed and admin:
        admin.table("generation_tasks").update(
            {"status": task_service.STATUS_RETRYING, "error_message": None, "error_category": None}
        ).in_("id", [t["id"] for t in failed]).execute()

    scenes = _fetch_scenes(client, project_id, only_failed=False)
    if not scenes:
        return {"status": "ok", "message": "No failed work to retry.", "task_count": 0}

    _assert_worker_can_write()

    pool = _load_pool(client, user_id)
    defaults = generation_defaults(client, user_id)
    result = await start_generation(project_id, scenes, pool, user_id=user_id, defaults=defaults)
    return {"status": "retrying", "project_id": project_id, **result}


@router.post("/scenes/{scene_id}/retry")
async def retry_scene(scene_id: int, token: str = Depends(get_token), user_id: str = Depends(verify_token)):
    """Retry a single scene from the task monitor (§39)."""
    client = get_db_client(token)
    scene_result = client.table("scenes").select("*").eq("id", scene_id).execute()
    if not scene_result.data:
        raise HTTPException(status_code=404, detail="Scene not found")

    scene = scene_result.data[0]
    project_id = scene["project_id"]

    admin = get_admin_client()
    if admin:
        admin.table("generation_tasks").update(
            {"status": task_service.STATUS_RETRYING, "error_message": None, "error_category": None}
        ).eq("scene_id", scene_id).in_(
            "status", [task_service.STATUS_FAILED, task_service.STATUS_CANCELLED]
        ).execute()

    if is_running(project_id):
        # The running job's scene queue was populated when it started, so this
        # scene will not be picked up by it. Say so plainly rather than
        # reporting a retry that will not happen.
        return {
            "status": "pending_next_run",
            "message": (
                "Scene reset for retry. A batch is currently running and will not pick it up — "
                "use Retry Failed once the current run finishes."
            ),
        }

    _assert_worker_can_write()

    pool = _load_pool(client, user_id)
    defaults = generation_defaults(client, user_id)
    result = await start_generation(project_id, [scene], pool, user_id=user_id, defaults=defaults)
    return {"status": "retrying", "scene_id": scene_id, **result}


@router.post("/scenes/{scene_id}/skip")
async def skip_scene(scene_id: int, token: str = Depends(get_token), user_id: str = Depends(verify_token)):
    client = get_db_client(token)
    if not client.table("scenes").select("id").eq("id", scene_id).execute().data:
        raise HTTPException(status_code=404, detail="Scene not found")

    admin = get_admin_client()
    if admin:
        admin.table("generation_tasks").update({"status": task_service.STATUS_SKIPPED}).eq(
            "scene_id", scene_id
        ).in_("status", sorted(task_service.OPEN_STATUSES)).execute()
    sync_scene_state(scene_id)
    return {"status": "skipped", "scene_id": scene_id}


@router.post("/{job_id}/refresh")
async def refresh_progress(job_id: Union[int, str], token: str = Depends(get_token), user_id: str = Depends(verify_token)):
    """Force a recount from the database (used after manual edits)."""
    client = get_db_client(token)
    project_id = await resolve_project_id(job_id, client)
    _assert_project_access(client, project_id)
    job = active_jobs.get(str(project_id)) or {}
    return await update_job_progress(job.get("job_id"), project_id)
