import asyncio
import logging
import os
import datetime
from typing import Dict, Any, List, Optional
from backend.services.google_ai_service import generate_image, generate_speech, RateLimitException, ProviderUnavailableException
from backend.services.ffmpeg_service import merge_image_audio
from backend.database import get_admin_client
from backend.config import settings

logger = logging.getLogger(__name__)

# Active background jobs state: project_id -> {"task": Task, "status": str, "job_id": int}
active_jobs: Dict[str, Dict[str, Any]] = {}
recovery_lock = asyncio.Lock()

async def update_job_progress(job_id: int, project_id: str):
    """Calculates aggregate metrics and updates generation_jobs and projects tables in Supabase."""
    client = get_admin_client()
    if not client or not job_id:
        return

    try:
        scenes_res = client.table("scenes").select("overall_status").eq("project_id", project_id).execute()
        scenes = scenes_res.data or []
        
        total = len(scenes)
        completed = sum(1 for s in scenes if s.get("overall_status") in ["COMPLETED", "completed"])
        failed = sum(1 for s in scenes if s.get("overall_status") in ["FAILED", "failed"])
        processing = sum(1 for s in scenes if s.get("overall_status") in ["PROCESSING", "generating", "VISUAL_GENERATING", "VOICE_GENERATING"])
        pending = total - (completed + failed + processing)
        
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        
        # Update generation_jobs
        job_update = {
            "total_tasks": total,
            "completed_tasks": completed,
            "failed_tasks": failed,
            "processing_tasks": processing,
            "pending_tasks": max(0, pending),
            "updated_at": now_iso
        }
        
        if pending == 0 and processing == 0 and total > 0:
            job_update["status"] = "COMPLETED" if failed == 0 else "FAILED"
            job_update["completed_at"] = now_iso
            
        client.table("generation_jobs").update(job_update).eq("id", job_id).execute()
        
        # Update projects table summary
        proj_update = {
            "total_scenes": total,
            "completed_scenes": completed,
            "failed_scenes": failed
        }
        if pending == 0 and processing == 0 and total > 0:
            proj_update["status"] = "COMPLETED" if failed == 0 else "FAILED"
            proj_update["finished_at"] = now_iso
        elif processing > 0 or pending > 0:
            proj_update["status"] = "PROCESSING"
            
        client.table("projects").update(proj_update).eq("id", project_id).execute()
    except Exception as e:
        logger.error(f"Failed to update job progress for job {job_id}: {e}")

async def process_scene(project_id: str, scene_id: str, scene_data: Dict[str, Any], api_key: str, job_id: Optional[int] = None):
    """Processes a single scene: Visual -> Voice -> Merge, updating granular statuses."""
    client = get_admin_client()
    try:
        if client:
            client.table("scenes").update({
                "visual_status": "VISUAL_GENERATING",
                "overall_status": "PROCESSING"
            }).eq("id", scene_id).execute()
            
        # 1. Visual Generation (Image)
        image_prompt = scene_data.get("visual_prompt", "")
        aspect_ratio = scene_data.get("aspect_ratio", "16:9")
        image_filename = f"{scene_id}.png"
        image_path = os.path.join(settings.images_dir, image_filename)
        
        if image_prompt and not os.path.exists(image_path):
            image_bytes = await generate_image(api_key, image_prompt, aspect_ratio=aspect_ratio)
            with open(image_path, "wb") as f:
                f.write(image_bytes)
                
        if client:
            client.table("scenes").update({
                "visual_status": "VISUAL_COMPLETED",
                "visual_path": f"/output/images/{image_filename}",
                "voice_status": "VOICE_GENERATING"
            }).eq("id", scene_id).execute()

        # 2. Voiceover Generation (Audio)
        audio_text = scene_data.get("voiceover_script", "")
        audio_filename = f"{scene_id}.mp3"
        audio_path = os.path.join(settings.audio_dir, audio_filename)
        
        if audio_text and not os.path.exists(audio_path):
            audio_bytes = await generate_speech(api_key, audio_text)
            with open(audio_path, "wb") as f:
                f.write(audio_bytes)
                
        if client:
            client.table("scenes").update({
                "voice_status": "VOICE_COMPLETED",
                "audio_path": f"/output/audio/{audio_filename}"
            }).eq("id", scene_id).execute()

        # 3. Merge Video (Image + Audio)
        video_filename = f"{scene_id}.mp4"
        video_path = os.path.join(settings.merged_dir, video_filename)
        
        if os.path.exists(image_path) and os.path.exists(audio_path) and not os.path.exists(video_path):
            await merge_image_audio(image_path, audio_path, video_path)
            
        if client:
            client.table("scenes").update({
                "overall_status": "COMPLETED",
                "visual_path": f"/output/images/{image_filename}",
                "audio_path": f"/output/audio/{audio_filename}",
                "merged_path": f"/output/merged/{video_filename}" if os.path.exists(video_path) else None,
                "error_message": None
            }).eq("id", scene_id).execute()
            
    except RateLimitException as rle:
        logger.warning(f"Rate limit hit during scene {scene_id}: {rle}")
        if client:
            client.table("scenes").update({
                "overall_status": "FAILED",
                "error_message": "Rate limit / Quota exceeded (HTTP 429). Job paused."
            }).eq("id", scene_id).execute()
        raise rle
    except Exception as e:
        logger.error(f"Error processing scene {scene_id}: {e}")
        if client:
            client.table("scenes").update({
                "overall_status": "FAILED",
                "error_message": str(e)
            }).eq("id", scene_id).execute()
    finally:
        if job_id:
            await update_job_progress(job_id, project_id)

async def generation_worker(queue: asyncio.Queue, api_key: str, job_id: Optional[int] = None):
    """Worker task that processes scenes from queue."""
    client = get_admin_client()
    while True:
        job = await queue.get()
        try:
            await process_scene(job["project_id"], job["scene_id"], job["scene_data"], api_key, job_id=job_id)
        except RateLimitException:
            logger.warning("Worker encountered rate limit. Pausing job queue processing.")
            if client and job_id:
                client.table("generation_jobs").update({"status": "PAUSED"}).eq("id", job_id).execute()
                client.table("projects").update({"status": "PAUSED"}).eq("id", job["project_id"]).execute()
            break
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Worker error: {e}")
        finally:
            queue.task_done()

async def start_generation(project_id: str, scenes: List[Dict[str, Any]], api_key: str, user_id: Optional[str] = None) -> int:
    """Starts background generation for scenes, creating a generation_jobs record."""
    if project_id in active_jobs and active_jobs[project_id].get("status") == "PROCESSING":
        return active_jobs[project_id]["job_id"]
        
    client = get_admin_client()
    job_id = None
    if client and user_id:
        try:
            now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
            job_res = client.table("generation_jobs").insert({
                "project_id": project_id,
                "user_id": user_id,
                "status": "PROCESSING",
                "total_tasks": len(scenes),
                "pending_tasks": len(scenes),
                "started_at": now_iso
            }).execute()
            if job_res.data:
                job_id = job_res.data[0]["id"]
                client.table("projects").update({"status": "PROCESSING", "started_at": now_iso}).eq("id", project_id).execute()
        except Exception as e:
            logger.error(f"Failed to create generation_job: {e}")

    queue = asyncio.Queue()
    for scene in scenes:
        await queue.put({"project_id": project_id, "scene_id": scene["id"], "scene_data": scene})
        
    workers = []
    for _ in range(settings.default_concurrency):
        worker = asyncio.create_task(generation_worker(queue, api_key, job_id=job_id))
        workers.append(worker)
        
    async def wait_and_cleanup():
        try:
            await queue.join()
        except asyncio.CancelledError:
            pass
        finally:
            for w in workers:
                w.cancel()
            if project_id in active_jobs:
                del active_jobs[project_id]
            if job_id:
                await update_job_progress(job_id, project_id)
            
    active_jobs[project_id] = {
        "task": asyncio.create_task(wait_and_cleanup()),
        "workers": workers,
        "status": "PROCESSING",
        "job_id": job_id
    }
    return job_id or 0

def pause_generation(project_id: str):
    """Pauses active generation for a project."""
    client = get_admin_client()
    if project_id in active_jobs:
        job_info = active_jobs[project_id]
        job_info["status"] = "PAUSED"
        for w in job_info.get("workers", []):
            w.cancel()
        job_info["task"].cancel()
        job_id = job_info.get("job_id")
        del active_jobs[project_id]
        
        if client:
            client.table("projects").update({"status": "PAUSED"}).eq("id", project_id).execute()
            if job_id:
                client.table("generation_jobs").update({"status": "PAUSED"}).eq("id", job_id).execute()

def cancel_generation(project_id: str):
    """Cancels active generation for a project."""
    client = get_admin_client()
    if project_id in active_jobs:
        job_info = active_jobs[project_id]
        for w in job_info.get("workers", []):
            w.cancel()
        job_info["task"].cancel()
        job_id = job_info.get("job_id")
        del active_jobs[project_id]
        
        if client:
            client.table("projects").update({"status": "CANCELLED"}).eq("id", project_id).execute()
            if job_id:
                client.table("generation_jobs").update({"status": "CANCELLED"}).eq("id", job_id).execute()

async def recover_pending_jobs():
    """Recovers scenes stuck in generating or pending status on startup."""
    client = get_admin_client()
    if not client:
        logger.warning("No admin client available, skipping job recovery.")
        return
        
    async with recovery_lock:
        logger.info("Checking for pending/generating scenes to recover...")
        try:
            projects_res = client.table("projects").select("id").in_("status", ["PENDING", "PROCESSING", "generating"]).execute()
            if not projects_res.data:
                return
                
            project_ids = [p["id"] for p in projects_res.data]
            
            for pid in project_ids:
                if str(pid) not in active_jobs:
                    scenes_res = client.table("scenes").select("*").eq("project_id", pid).in_("overall_status", ["pending", "generating", "PENDING", "PROCESSING", "VISUAL_GENERATING", "VOICE_GENERATING"]).execute()
                    if scenes_res.data:
                        logger.info(f"Recovering {len(scenes_res.data)} scenes for project {pid}")
                        client.table("scenes").update({
                            "overall_status": "FAILED",
                            "error_message": "Server restarted during generation. Please retry."
                        }).eq("project_id", pid).in_("overall_status", ["pending", "generating", "PENDING", "PROCESSING", "VISUAL_GENERATING", "VOICE_GENERATING"]).execute()
                        
                        client.table("projects").update({"status": "FAILED"}).eq("id", pid).execute()
        except Exception as e:
            logger.error(f"Failed to recover jobs: {e}")
