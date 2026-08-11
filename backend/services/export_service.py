import os
import zipfile
import tempfile
import uuid
import logging
from typing import List, Dict, Any, Optional
from backend.config import settings

logger = logging.getLogger(__name__)

async def create_filtered_export_zip(project_name: str, scenes: List[Dict[str, Any]], file_types: Optional[List[str]] = None) -> str:
    """Creates a ZIP archive organized into folders (images/, voiceovers/, videos/)."""
    if file_types is None:
        file_types = ["images", "voiceovers", "videos"]
        
    export_id = str(uuid.uuid4())
    clean_proj_name = "".join([c if c.isalnum() else "_" for c in project_name])
    zip_filename = f"export_{clean_proj_name}_{export_id[:8]}.zip"
    zip_path = os.path.join(settings.output_dir, zip_filename)
    
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for scene in scenes:
            scene_num = scene.get("scene_number") or scene.get("id")
            
            # Images
            if "images" in file_types and (scene.get("visual_path") or scene.get("image_url")):
                path = scene.get("visual_path") or scene.get("image_url")
                local_rel = path.lstrip("/output/")
                local_path = os.path.join(settings.output_dir, local_rel.replace("/", os.sep))
                if os.path.exists(local_path):
                    arcname = os.path.join(clean_proj_name, "images", os.path.basename(local_path))
                    zipf.write(local_path, arcname)

            # Voiceovers (Audio)
            if "voiceovers" in file_types and (scene.get("audio_path") or scene.get("audio_url")):
                path = scene.get("audio_path") or scene.get("audio_url")
                local_rel = path.lstrip("/output/")
                local_path = os.path.join(settings.output_dir, local_rel.replace("/", os.sep))
                if os.path.exists(local_path):
                    arcname = os.path.join(clean_proj_name, "voiceovers", os.path.basename(local_path))
                    zipf.write(local_path, arcname)

            # Videos (Merged)
            if "videos" in file_types and (scene.get("merged_path") or scene.get("video_url")):
                path = scene.get("merged_path") or scene.get("video_url")
                local_rel = path.lstrip("/output/")
                local_path = os.path.join(settings.output_dir, local_rel.replace("/", os.sep))
                if os.path.exists(local_path):
                    arcname = os.path.join(clean_proj_name, "videos", os.path.basename(local_path))
                    zipf.write(local_path, arcname)
                    
    return f"/output/{zip_filename}"
