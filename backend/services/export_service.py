import os
import zipfile
import uuid
import logging
from backend.config import settings
from typing import List

logger = logging.getLogger(__name__)

def create_export_zip(project_id: str, files: List[str]) -> str:
    """Creates a ZIP file containing the specified files."""
    export_id = str(uuid.uuid4())
    zip_filename = f"export_{project_id}_{export_id}.zip"
    zip_path = os.path.join(settings.output_dir, zip_filename)
    
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for file_path in files:
            # Reconstruct the absolute path if it was passed as a URL path
            if file_path.startswith("/output/"):
                file_path = os.path.join(settings.base_dir, file_path.lstrip("/"))
                
            if os.path.exists(file_path):
                # Add file to zip with a relative path based on its type
                if file_path.startswith(settings.images_dir):
                    arcname = f"images/{os.path.basename(file_path)}"
                elif file_path.startswith(settings.audio_dir):
                    arcname = f"audio/{os.path.basename(file_path)}"
                elif file_path.startswith(settings.videos_dir):
                    arcname = f"videos/{os.path.basename(file_path)}"
                elif file_path.startswith(settings.merged_dir):
                    arcname = f"merged/{os.path.basename(file_path)}"
                else:
                    arcname = os.path.basename(file_path)
                
                zipf.write(file_path, arcname)
            else:
                logger.warning(f"File not found for export: {file_path}")
                
    return zip_path
