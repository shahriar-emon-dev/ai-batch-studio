import asyncio
import logging
import os
from typing import Dict, Any, List
from backend.services.google_ai_service import generate_image, generate_speech
from backend.services.ffmpeg_service import merge_image_audio
from backend.database import admin_client
from backend.config import settings

logger = logging.getLogger(__name__)

# In-memory state for jobs
active_jobs: Dict[str, asyncio.Task] = {}
recovery_lock = asyncio.Lock()

async def process_scene(project_id: str, scene_id: str, scene_data: Dict[str, Any], api_key: str):
    """Processes a single scene: Image -> Audio -> Video."""
    try:
        if admin_client:
            admin_client.table("scenes").update({"status": "generating"}).eq("id", scene_id).execute()
            
        # 1. Generate Image
        image_prompt = scene_data.get("visual_prompt", "")
        image_filename = f"{scene_id}.png"
        image_path = os.path.join(settings.images_dir, image_filename)
        
        if image_prompt and not os.path.exists(image_path):
            image_bytes = await generate_image(api_key, image_prompt)
            with open(image_path, "wb") as f:
                f.write(image_bytes)
                
        # 2. Generate Audio
        audio_text = scene_data.get("voiceover_script", "")
        audio_filename = f"{scene_id}.mp3"
        audio_path = os.path.join(settings.audio_dir, audio_filename)
        
        if audio_text and not os.path.exists(audio_path):
            audio_bytes = await generate_speech(api_key, audio_text)
            with open(audio_path, "wb") as f:
                f.write(audio_bytes)
                
        # 3. Merge Video
        video_filename = f"{scene_id}.mp4"
        video_path = os.path.join(settings.merged_dir, video_filename)
        
        if os.path.exists(image_path) and os.path.exists(audio_path) and not os.path.exists(video_path):
            await merge_image_audio(image_path, audio_path, video_path)
            
        if admin_client:
            admin_client.table("scenes").update({
                "status": "completed",
                "image_url": f"/output/images/{image_filename}",
                "audio_url": f"/output/audio/{audio_filename}",
                "video_url": f"/output/merged/{video_filename}"
            }).eq("id", scene_id).execute()
            
    except Exception as e:
        logger.error(f"Error processing scene {scene_id}: {e}")
        if admin_client:
            admin_client.table("scenes").update({
                "status": "failed",
                "error_message": str(e)
            }).eq("id", scene_id).execute()
            
async def generation_worker(queue: asyncio.Queue, api_key: str):
    """Worker task that processes scenes from the queue."""
    while True:
        job = await queue.get()
        try:
            await process_scene(job["project_id"], job["scene_id"], job["scene_data"], api_key)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Worker error: {e}")
        finally:
            queue.task_done()

async def start_generation(project_id: str, scenes: List[Dict[str, Any]], api_key: str):
    """Starts background generation for a list of scenes."""
    if project_id in active_jobs:
        return  # Already running
        
    queue = asyncio.Queue()
    for scene in scenes:
        await queue.put({"project_id": project_id, "scene_id": scene["id"], "scene_data": scene})
        
    # Create workers
    workers = []
    for _ in range(settings.default_concurrency):
        worker = asyncio.create_task(generation_worker(queue, api_key))
        workers.append(worker)
        
    # Task to wait for queue completion and clean up
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
            
    active_jobs[project_id] = asyncio.create_task(wait_and_cleanup())

def cancel_generation(project_id: str):
    """Cancels an active generation job."""
    if project_id in active_jobs:
        active_jobs[project_id].cancel()
        del active_jobs[project_id]

async def recover_pending_jobs():
    """Recovers scenes stuck in generating or pending status on startup."""
    if not admin_client:
        logger.warning("No admin client available, skipping job recovery.")
        return
        
    async with recovery_lock:
        logger.info("Checking for pending/generating scenes to recover...")
        try:
            # Find all projects that are processing but have no active jobs
            projects_res = admin_client.table("projects").select("id").in_("status", ["PENDING", "PROCESSING", "generating"]).execute()
            
            if not projects_res.data:
                return
                
            project_ids = [p["id"] for p in projects_res.data]
            
            # For each project, fetch pending/generating scenes and start generation
            for pid in project_ids:
                if str(pid) not in active_jobs:
                    scenes_res = admin_client.table("scenes").select("*").eq("project_id", pid).in_("status", ["pending", "generating", "PENDING", "PROCESSING"]).execute()
                    if scenes_res.data:
                        logger.info(f"Recovering {len(scenes_res.data)} scenes for project {pid}")
                        
                        # We need the API key for recovery. This is a bit tricky if we don't know the user's key.
                        # For now, we update them to 'failed' so the user can manually retry, rather than leaving them hung forever.
                        # Since we can't reliably read encrypted API keys without the user context easily here.
                        admin_client.table("scenes").update({"status": "failed", "error_message": "Server restarted during generation. Please retry."}).eq("project_id", pid).in_("status", ["pending", "generating", "PENDING", "PROCESSING"]).execute()
                        admin_client.table("projects").update({"status": "FAILED"}).eq("id", pid).execute()
        except Exception as e:
            logger.error(f"Failed to recover jobs: {e}")
