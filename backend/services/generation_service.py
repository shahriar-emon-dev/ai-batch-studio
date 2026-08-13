"""Background generation orchestrator (proposal §32, §35, §37, §40, §51).

The queue is task-based: every scene is expanded into generation_tasks rows and
each task is executed, retried, and recorded independently. Progress reported to
the frontend is always derived from those rows — never from an estimate.
"""

import asyncio
import datetime
import logging
import os
from typing import Any, Dict, List, Optional

from backend.config import settings
from backend.database import get_admin_client
from backend.services import asset_service, task_service
from backend.services.api_profile_service import (
    ApiProfilePool,
    NoAvailableProfileError,
    call_with_rotation,
)
from backend.services.audit_service import log_activity, log_error, summarize_error
from backend.services.ffmpeg_service import is_available as ffmpeg_available
from backend.services.ffmpeg_service import merge_image_audio, merge_video_audio
from backend.services.google_ai_service import (
    ProviderUnavailableException,
    generate_image,
    generate_speech,
    generate_video,
)
from backend.services.prompt_service import compose_voice_request, negative_prompt_for

logger = logging.getLogger(__name__)

# project_id (str) -> runtime state for an in-flight job
active_jobs: Dict[str, Dict[str, Any]] = {}
recovery_lock = asyncio.Lock()

IMAGE_EXTENSIONS = ("png", "jpg", "jpeg", "webp")
AUDIO_EXTENSIONS = ("mp3", "wav")


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class JobControl:
    """Cooperative pause/cancel signalling for a project's workers."""

    def __init__(self) -> None:
        self.cancelled = asyncio.Event()
        self.paused = asyncio.Event()

    @property
    def stopping(self) -> bool:
        return self.cancelled.is_set() or self.paused.is_set()


# ---------------------------------------------------------------------------
# Scene + job state derived from task rows
# ---------------------------------------------------------------------------

def _scene_status_from_tasks(tasks: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Translate a scene's task rows into the scene columns the UI reads."""
    by_type = {t.get("task_type"): t for t in tasks}

    def granular(task_type: str, generating: str, completed: str) -> str:
        task = by_type.get(task_type)
        if not task:
            return "PENDING"
        status = task.get("status")
        if status == task_service.STATUS_COMPLETED:
            return completed
        if status in task_service.ACTIVE_STATUSES:
            return generating
        if status == task_service.STATUS_UNSUPPORTED:
            return "UNSUPPORTED"
        if status == task_service.STATUS_FAILED:
            return "FAILED"
        if status == task_service.STATUS_CANCELLED:
            return "CANCELLED"
        return "PENDING"

    statuses = [t.get("status") for t in tasks]
    generated = [t for t in tasks if t.get("task_type") != task_service.TASK_MERGE]

    if not tasks:
        overall = "PENDING"
    elif any(s in task_service.ACTIVE_STATUSES for s in statuses):
        overall = "PROCESSING"
    elif any(s == task_service.STATUS_FAILED for s in statuses):
        overall = "FAILED"
    elif any(s in (task_service.STATUS_PENDING, task_service.STATUS_QUEUED) for s in statuses):
        overall = "PENDING"
    elif any(s == task_service.STATUS_CANCELLED for s in statuses):
        overall = "CANCELLED"
    elif generated and all(
        t.get("status") in (task_service.STATUS_UNSUPPORTED, task_service.STATUS_SKIPPED)
        for t in generated
    ):
        overall = "SKIPPED"
    elif any(s == task_service.STATUS_COMPLETED for s in statuses):
        overall = "COMPLETED"
    else:
        overall = "PENDING"

    errors = [t.get("error_message") for t in tasks if t.get("status") == task_service.STATUS_FAILED]

    return {
        "visual_status": granular(task_service.TASK_IMAGE, "VISUAL_GENERATING", "VISUAL_COMPLETED"),
        "voice_status": granular(task_service.TASK_VOICEOVER, "VOICE_GENERATING", "VOICE_COMPLETED"),
        "video_status": granular(task_service.TASK_VIDEO, "PROCESSING", "COMPLETED"),
        "merge_status": granular(task_service.TASK_MERGE, "MERGING", "COMPLETED"),
        "overall_status": overall,
        "error_message": errors[0] if errors else None,
    }


def sync_scene_state(scene_id: Any) -> Dict[str, Any]:
    """Recompute one scene's columns from its tasks and persist them."""
    client = get_admin_client()
    if not client:
        return {}
    try:
        tasks = (
            client.table("generation_tasks")
            .select("task_type, status, error_message, storage_path")
            .eq("scene_id", scene_id)
            .execute()
            .data
            or []
        )
    except Exception as exc:
        logger.error("Could not load tasks for scene %s: %s", scene_id, exc)
        return {}

    updates = _scene_status_from_tasks(tasks)

    # Paths come from completed tasks only, so a path never implies a
    # completion that did not happen (§34).
    path_columns = {
        task_service.TASK_IMAGE: "visual_path",
        task_service.TASK_VOICEOVER: "audio_path",
        task_service.TASK_VIDEO: "video_path",
        task_service.TASK_MERGE: "merged_path",
    }
    for task in tasks:
        column = path_columns.get(task.get("task_type"))
        if column and task.get("status") == task_service.STATUS_COMPLETED:
            updates[column] = task.get("storage_path")

    updates["updated_at"] = _now_iso()
    try:
        client.table("scenes").update(updates).eq("id", scene_id).execute()
    except Exception as exc:
        logger.error("Could not update scene %s: %s", scene_id, exc)
    return updates


async def update_job_progress(job_id: Optional[int], project_id: Any) -> Dict[str, Any]:
    """Roll task counts up into generation_jobs and projects (§35, §55)."""
    client = get_admin_client()
    if not client:
        return {}

    # Counted across the whole project, not just this job: tasks completed by an
    # earlier run must still show as done after a refresh or resume (§38).
    progress = task_service.compute_progress(client, project_id)
    overall = progress["overall"]
    now = _now_iso()

    job_update = {
        "total_tasks": overall["total"],
        "completed_tasks": overall["completed"],
        "failed_tasks": overall["failed"],
        "processing_tasks": overall["processing"],
        "pending_tasks": overall["pending"],
        "updated_at": now,
    }

    settled = overall["pending"] == 0 and overall["processing"] == 0 and overall["total"] > 0
    if settled:
        job_update["status"] = "COMPLETED" if overall["failed"] == 0 else "FAILED"
        job_update["completed_at"] = now

    if job_id:
        try:
            client.table("generation_jobs").update(job_update).eq("id", job_id).execute()
        except Exception as exc:
            logger.error("Job %s progress update failed: %s", job_id, exc)

    # Project-level scene counters stay scene-based: that is what the
    # dashboard and project list display.
    try:
        scenes = (
            client.table("scenes")
            .select("overall_status")
            .eq("project_id", str(project_id))
            .execute()
            .data
            or []
        )
        completed = sum(1 for s in scenes if s.get("overall_status") == "COMPLETED")
        failed = sum(1 for s in scenes if s.get("overall_status") == "FAILED")
        skipped = sum(1 for s in scenes if s.get("overall_status") in ("SKIPPED", "CANCELLED"))
        active = sum(1 for s in scenes if s.get("overall_status") in ("PROCESSING", "PENDING"))

        project_update: Dict[str, Any] = {
            "total_scenes": len(scenes),
            "completed_scenes": completed,
            "failed_scenes": failed,
            "skipped_scenes": skipped,
            "updated_at": now,
        }
        if active == 0 and scenes:
            project_update["status"] = "COMPLETED" if failed == 0 else "FAILED"
            project_update["finished_at"] = now
        elif str(project_id) in active_jobs:
            project_update["status"] = "PROCESSING"

        client.table("projects").update(project_update).eq("id", str(project_id)).execute()
    except Exception as exc:
        logger.error("Project %s progress update failed: %s", project_id, exc)

    return progress


# ---------------------------------------------------------------------------
# Task execution
# ---------------------------------------------------------------------------

async def _execute_image(scene, task, pool, defaults):
    negative = negative_prompt_for(scene, defaults)
    aspect = scene.get("aspect_ratio") or defaults.get("default_aspect_ratio") or "16:9"
    data, profile = await call_with_rotation(
        pool, generate_image, task["prompt"], aspect, negative
    )
    absolute, url = asset_service.write_asset_file("image", scene["id"], "png", data)
    return absolute, url, profile, settings.image_model


async def _execute_voiceover(scene, task, pool, defaults):
    script, options = compose_voice_request(scene, defaults)
    result, profile = await call_with_rotation(
        pool,
        generate_speech,
        script or task["prompt"],
        options["voice"],
        options["language"],
        options["speed"],
        options["pitch"],
    )
    data, extension = result
    absolute, url = asset_service.write_asset_file("voiceover", scene["id"], extension, data)
    return absolute, url, profile, "google-tts"


async def _execute_video(scene, task, pool, defaults):
    negative = negative_prompt_for(scene, defaults)
    aspect = scene.get("aspect_ratio") or defaults.get("default_aspect_ratio") or "16:9"
    data, profile = await call_with_rotation(
        pool, generate_video, task["prompt"], aspect, scene.get("duration"), negative
    )
    absolute, url = asset_service.write_asset_file("video", scene["id"], "mp4", data)
    return absolute, url, profile, settings.video_model


async def _execute_merge(scene, task, pool, defaults):
    """Merge is local work — no API profile involved."""
    if not ffmpeg_available():
        raise ProviderUnavailableException(
            "FFmpeg is not installed on this server, so image+voiceover merging is unavailable."
        )

    audio = asset_service.stored_asset_path("voiceover", scene["id"], AUDIO_EXTENSIONS)
    if not audio:
        raise ProviderUnavailableException("No voiceover available to merge")

    video = asset_service.stored_asset_path("video", scene["id"], ("mp4",))
    output_absolute, output_url = asset_service.storage_paths("merged", scene["id"], "mp4")

    if video:
        await merge_video_audio(video[0], audio[0], output_absolute)
    else:
        image = asset_service.stored_asset_path("image", scene["id"], IMAGE_EXTENSIONS)
        if not image:
            raise ProviderUnavailableException("No image available to merge")
        await merge_image_audio(image[0], audio[0], output_absolute)

    return output_absolute, output_url, None, "ffmpeg"


TASK_EXECUTORS = {
    task_service.TASK_IMAGE: _execute_image,
    task_service.TASK_VOICEOVER: _execute_voiceover,
    task_service.TASK_VIDEO: _execute_video,
    task_service.TASK_MERGE: _execute_merge,
}

ASSET_TYPE_FOR_TASK = {
    task_service.TASK_IMAGE: "image",
    task_service.TASK_VOICEOVER: "voiceover",
    task_service.TASK_VIDEO: "video",
    task_service.TASK_MERGE: "merged",
}

EXISTING_EXTENSIONS = {
    task_service.TASK_IMAGE: IMAGE_EXTENSIONS,
    task_service.TASK_VOICEOVER: AUDIO_EXTENSIONS,
    task_service.TASK_VIDEO: ("mp4",),
    task_service.TASK_MERGE: ("mp4",),
}


def _merge_is_stale(scene_id: Any, merged_path: str) -> bool:
    """True when the merged output predates the image/voiceover it was built from."""
    try:
        merged_at = os.path.getmtime(merged_path)
    except OSError:
        return True

    newest_input = max(
        asset_service.newest_mtime("image", scene_id, IMAGE_EXTENSIONS),
        asset_service.newest_mtime("voiceover", scene_id, AUDIO_EXTENSIONS),
        asset_service.newest_mtime("video", scene_id, ("mp4",)),
    )
    return newest_input > merged_at


async def process_task(
    scene: Dict[str, Any],
    task: Dict[str, Any],
    pool: ApiProfilePool,
    defaults: Dict[str, Any],
    user_id: Optional[str],
    project_id: Any,
) -> str:
    """Run one task to a terminal state. Returns the final status."""
    task_id = task.get("id")
    task_type = task["task_type"]
    scene_number = scene.get("scene_number") or scene.get("id")
    asset_type = ASSET_TYPE_FOR_TASK[task_type]
    attempt = (task.get("attempt_count") or 0) + 1

    # Idempotency: a verified file on disk means this task is already done.
    # A merge is the exception — it must be redone when either input is newer
    # than the merged output, otherwise a regenerated image or voiceover would
    # silently keep the previous video.
    existing = asset_service.stored_asset_path(asset_type, scene["id"], EXISTING_EXTENSIONS[task_type])
    if existing and task_type == task_service.TASK_MERGE and _merge_is_stale(scene["id"], existing[0]):
        existing = None

    if existing and task.get("status") != task_service.STATUS_COMPLETED:
        absolute, url = existing
        asset_service.register_asset(
            user_id, project_id, scene["id"], asset_type, absolute, url,
            scene_number=scene_number, task_id=task_id,
            display_filename=_display_filename(scene, asset_type, absolute),
            prompt=task.get("prompt") or "",
        )
        task_service.mark_completed(task_id, url)
        return task_service.STATUS_COMPLETED

    task_service.mark_processing(task_id, attempt)
    log_activity(project_id, user_id, "INFO", f"Scene {scene_number} — {task_type} generation started",
                 scene_id=scene["id"], scene_number=scene_number)

    try:
        absolute, url, profile, model = await TASK_EXECUTORS[task_type](scene, task, pool, defaults)
    except ProviderUnavailableException as exc:
        task_service.mark_unsupported(task_id, str(exc))
        log_activity(project_id, user_id, "WARNING",
                     f"Scene {scene_number} — {task_type} unsupported: {exc}",
                     scene_id=scene["id"], scene_number=scene_number)
        log_error(user_id, project_id, "UNSUPPORTED", str(exc), scene_id=scene["id"], task_id=task_id)
        return task_service.STATUS_UNSUPPORTED
    except NoAvailableProfileError:
        # Not the task's fault — leave it queued so a resume picks it up.
        task_service.mark_status(task_id, task_service.STATUS_QUEUED)
        raise
    except Exception as exc:
        details = summarize_error(exc)
        task_service.mark_failed(task_id, details["category"], details["message"], attempt)
        log_activity(project_id, user_id, "ERROR",
                     f"Scene {scene_number} — {task_type} failed: {details['message']}",
                     scene_id=scene["id"], scene_number=scene_number)
        log_error(user_id, project_id, details["category"], details["message"],
                  scene_id=scene["id"], task_id=task_id, is_retryable=details["retryable"],
                  attempt_count=attempt)
        return task_service.STATUS_FAILED

    profile_id = profile.get("id") if profile else None
    asset = asset_service.register_asset(
        user_id, project_id, scene["id"], asset_type, absolute, url,
        scene_number=scene_number, task_id=task_id,
        display_filename=_display_filename(scene, asset_type, absolute),
        prompt=task.get("prompt") or "", model=model,
        provider="ffmpeg" if task_type == task_service.TASK_MERGE else "google",
    )

    if not asset:
        message = "Generated output could not be verified in storage"
        task_service.mark_failed(task_id, "STORAGE_ERROR", message, attempt)
        log_error(user_id, project_id, "STORAGE_ERROR", message,
                  scene_id=scene["id"], task_id=task_id, is_retryable=True, attempt_count=attempt)
        return task_service.STATUS_FAILED

    task_service.mark_completed(task_id, url, profile_id)
    log_activity(project_id, user_id, "SUCCESS",
                 f"Scene {scene_number} — {task_type} completed ({os.path.basename(absolute)})",
                 scene_id=scene["id"], scene_number=scene_number)
    return task_service.STATUS_COMPLETED


def _display_filename(scene: Dict[str, Any], asset_type: str, absolute_path: str) -> str:
    """Human-friendly name used in exports; storage keys stay id-based."""
    extension = os.path.splitext(absolute_path)[1]
    base = asset_service.safe_filename(
        scene.get("filename") or "", f"scene_{scene.get('scene_number') or scene.get('id')}"
    )
    base = os.path.splitext(base)[0]
    suffix = {"image": "", "voiceover": "_voiceover", "video": "_video", "merged": "_merged"}[asset_type]
    return f"{base}{suffix}{extension}"


# ---------------------------------------------------------------------------
# Workers
# ---------------------------------------------------------------------------

async def _process_scene(
    scene: Dict[str, Any],
    pool: ApiProfilePool,
    defaults: Dict[str, Any],
    user_id: Optional[str],
    project_id: Any,
    job_id: Optional[int],
    control: JobControl,
) -> None:
    client = get_admin_client()
    if not client:
        return

    try:
        tasks = (
            client.table("generation_tasks")
            .select("*")
            .eq("scene_id", scene["id"])
            .in_("status", sorted(task_service.OPEN_STATUSES))
            .execute()
            .data
            or []
        )
    except Exception as exc:
        logger.error("Could not load tasks for scene %s: %s", scene["id"], exc)
        return

    # Merge depends on the other assets, so it always runs last.
    order = {task_service.TASK_IMAGE: 0, task_service.TASK_VOICEOVER: 1,
             task_service.TASK_VIDEO: 2, task_service.TASK_MERGE: 3}
    tasks.sort(key=lambda t: order.get(t.get("task_type"), 9))

    for task in tasks:
        if control.stopping:
            task_service.mark_status(
                task["id"],
                task_service.STATUS_CANCELLED if control.cancelled.is_set() else task_service.STATUS_QUEUED,
            )
            continue
        await process_task(scene, task, pool, defaults, user_id, project_id)

    sync_scene_state(scene["id"])
    await update_job_progress(job_id, project_id)


async def generation_worker(
    queue: asyncio.Queue,
    pool: ApiProfilePool,
    defaults: Dict[str, Any],
    user_id: Optional[str],
    project_id: Any,
    job_id: Optional[int],
    control: JobControl,
) -> None:
    while True:
        try:
            scene = queue.get_nowait()
        except asyncio.QueueEmpty:
            return

        try:
            if control.stopping:
                return
            await _process_scene(scene, pool, defaults, user_id, project_id, job_id, control)
        except NoAvailableProfileError as exc:
            # Every key is parked. Pause rather than failing every remaining
            # task, and record the provider's own words as the reason (§13).
            logger.warning("Pausing project %s: %s", project_id, exc)
            control.paused.set()
            await _pause_job_state(project_id, job_id, user_id, str(exc))
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Worker error on scene %s: %s", scene.get("id"), exc)
        finally:
            queue.task_done()


async def _pause_job_state(project_id: Any, job_id: Optional[int], user_id: Optional[str], reason: str) -> None:
    client = get_admin_client()
    if not client:
        return
    try:
        client.table("projects").update({"status": "PAUSED"}).eq("id", str(project_id)).execute()
        if job_id:
            client.table("generation_jobs").update({"status": "PAUSED"}).eq("id", job_id).execute()
    except Exception as exc:
        logger.error("Could not mark project %s paused: %s", project_id, exc)
    log_activity(project_id, user_id, "WARNING", f"Generation paused — {reason}")


# ---------------------------------------------------------------------------
# Public API used by the routers
# ---------------------------------------------------------------------------

async def start_generation(
    project_id: Any,
    scenes: List[Dict[str, Any]],
    pool: ApiProfilePool,
    user_id: Optional[str] = None,
    defaults: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Plan tasks for the given scenes and run them in the background."""
    pid = str(project_id)
    defaults = defaults or {}

    if pid in active_jobs:
        return {"job_id": active_jobs[pid].get("job_id"), "status": "ALREADY_RUNNING", "task_count": 0}

    client = get_admin_client()
    if not client:
        raise RuntimeError("Database is not configured; cannot start generation")

    job_id: Optional[int] = None
    try:
        job = (
            client.table("generation_jobs")
            .insert(
                {
                    "project_id": int(pid),
                    "user_id": user_id,
                    "status": "PROCESSING",
                    "started_at": _now_iso(),
                }
            )
            .execute()
        )
        if job.data:
            job_id = job.data[0]["id"]
    except Exception as exc:
        logger.error("Could not create generation job for project %s: %s", pid, exc)

    # Expand scenes into tasks (§32). Scenes with nothing to generate are skipped.
    task_count = 0
    runnable: List[Dict[str, Any]] = []
    for scene in scenes:
        planned = task_service.plan_tasks(scene, defaults)
        if not planned:
            client.table("scenes").update(
                {"overall_status": "SKIPPED", "error_message": "No generatable content in this row"}
            ).eq("id", scene["id"]).execute()
            continue
        for item in planned:
            row = task_service.upsert_task(user_id, pid, job_id, scene, item["task_type"], item["prompt"])
            if row and row.get("status") != task_service.STATUS_COMPLETED:
                task_count += 1
        runnable.append(scene)

    if not runnable:
        if job_id:
            client.table("generation_jobs").update(
                {"status": "COMPLETED", "completed_at": _now_iso()}
            ).eq("id", job_id).execute()
        return {"job_id": job_id, "status": "NOTHING_TO_DO", "task_count": 0}

    try:
        client.table("projects").update({"status": "PROCESSING", "started_at": _now_iso()}).eq("id", pid).execute()
    except Exception as exc:
        logger.error("Could not mark project %s processing: %s", pid, exc)

    log_activity(pid, user_id, "INFO",
                 f"Batch generation started — {len(runnable)} scenes, {task_count} tasks queued")

    queue: asyncio.Queue = asyncio.Queue()
    for scene in runnable:
        queue.put_nowait(scene)

    control = JobControl()
    concurrency = max(1, int(defaults.get("concurrency") or settings.default_concurrency))
    workers = [
        asyncio.create_task(
            generation_worker(queue, pool, defaults, user_id, pid, job_id, control)
        )
        for _ in range(min(concurrency, len(runnable)))
    ]

    async def supervise() -> None:
        try:
            await asyncio.gather(*workers, return_exceptions=True)
        finally:
            active_jobs.pop(pid, None)
            await update_job_progress(job_id, pid)
            if not control.stopping:
                log_activity(pid, user_id, "INFO", "Batch generation finished")

    active_jobs[pid] = {
        "job_id": job_id,
        "user_id": user_id,
        "control": control,
        "workers": workers,
        "task": asyncio.create_task(supervise()),
        "started_at": _now_iso(),
    }

    return {"job_id": job_id, "status": "STARTED", "task_count": task_count, "scene_count": len(runnable)}


async def pause_generation(project_id: Any, user_id: Optional[str] = None) -> bool:
    """Stop scheduling new tasks; in-flight tasks finish and stay COMPLETED."""
    pid = str(project_id)
    job = active_jobs.get(pid)
    if not job:
        # Nothing running — still reflect the intent in the database.
        await _pause_job_state(pid, None, user_id, "paused by user")
        return False

    job["control"].paused.set()
    await _pause_job_state(pid, job.get("job_id"), job.get("user_id") or user_id, "paused by user")
    return True


async def cancel_generation(project_id: Any, user_id: Optional[str] = None) -> bool:
    """Cancel remaining work. Completed assets are preserved (§39)."""
    pid = str(project_id)
    job = active_jobs.get(pid)
    client = get_admin_client()

    if job:
        job["control"].cancelled.set()
        for worker in job.get("workers", []):
            worker.cancel()

    task_service.cancel_open_tasks(pid)

    if client:
        try:
            client.table("projects").update({"status": "CANCELLED"}).eq("id", pid).execute()
            if job and job.get("job_id"):
                client.table("generation_jobs").update(
                    {"status": "CANCELLED", "completed_at": _now_iso()}
                ).eq("id", job["job_id"]).execute()
        except Exception as exc:
            logger.error("Could not mark project %s cancelled: %s", pid, exc)

    log_activity(pid, (job or {}).get("user_id") or user_id, "WARNING", "Generation cancelled by user")
    active_jobs.pop(pid, None)
    return bool(job)


def is_running(project_id: Any) -> bool:
    return str(project_id) in active_jobs


async def recover_pending_jobs() -> None:
    """On startup, no worker owns any task — re-open whatever was in flight (§38)."""
    client = get_admin_client()
    if not client:
        logger.warning("No database client available; skipping job recovery")
        return

    async with recovery_lock:
        try:
            stuck = (
                client.table("generation_tasks")
                .select("id, scene_id, project_id")
                .in_("status", [task_service.STATUS_PROCESSING, task_service.STATUS_RETRYING])
                .execute()
                .data
                or []
            )
            if stuck:
                logger.info("Recovering %d task(s) interrupted by a restart", len(stuck))
                client.table("generation_tasks").update(
                    {
                        "status": task_service.STATUS_FAILED,
                        "error_category": "INTERRUPTED",
                        "error_message": "Server restarted during generation. Retry to continue.",
                        "updated_at": _now_iso(),
                    }
                ).in_("id", [t["id"] for t in stuck]).execute()

                for scene_id in {t["scene_id"] for t in stuck if t.get("scene_id")}:
                    sync_scene_state(scene_id)

            # Any job/project left mid-flight has no owner after a restart.
            client.table("generation_jobs").update({"status": "PAUSED"}).eq("status", "PROCESSING").execute()
            client.table("projects").update({"status": "PAUSED"}).eq("status", "PROCESSING").execute()
        except Exception as exc:
            logger.error("Job recovery failed: %s", exc)

        await reconcile_missing_media()


async def reconcile_missing_media() -> int:
    """Drop completion state for assets whose files are no longer on disk.

    Deployment targets with an ephemeral filesystem lose `output/` on every
    restart while the database rows survive. Without this the UI would keep
    showing a completed ✓ and a broken thumbnail for media that no longer
    exists, which is exactly the false-completion the spec forbids (§34).
    Affected tasks return to QUEUED so a resume regenerates them.
    """
    client = get_admin_client()
    if not client:
        return 0

    try:
        assets = client.table("assets").select("id, scene_id, asset_type, storage_path").execute().data or []
    except Exception as exc:
        logger.error("Could not read assets for reconciliation: %s", exc)
        return 0

    missing = [
        asset for asset in assets
        if not (lambda p: p and os.path.exists(p))(asset_service.local_path_for_url(asset.get("storage_path")))
    ]
    if not missing:
        return 0

    logger.warning("%d asset file(s) missing from storage; clearing their completion state", len(missing))
    scene_ids = sorted({a["scene_id"] for a in missing if a.get("scene_id")})

    try:
        client.table("assets").delete().in_("id", [a["id"] for a in missing]).execute()

        for asset in missing:
            if not asset.get("scene_id"):
                continue
            task_type = {"image": task_service.TASK_IMAGE, "voiceover": task_service.TASK_VOICEOVER,
                         "video": task_service.TASK_VIDEO, "merged": task_service.TASK_MERGE}.get(asset["asset_type"])
            if not task_type:
                continue
            client.table("generation_tasks").update(
                {
                    "status": task_service.STATUS_QUEUED,
                    "storage_path": None,
                    "error_category": "MEDIA_MISSING",
                    "error_message": "Stored file was lost (server restart). Queued for regeneration.",
                    "updated_at": _now_iso(),
                }
            ).eq("scene_id", asset["scene_id"]).eq("task_type", task_type).execute()

        for scene_id in scene_ids:
            sync_scene_state(scene_id)
    except Exception as exc:
        logger.error("Media reconciliation failed: %s", exc)
        return 0

    return len(missing)
