import csv
import io
import chardet
from typing import List, Dict, Any
from fastapi import APIRouter, UploadFile, File, HTTPException, Depends
from backend.auth import get_token, verify_token

router = APIRouter()

VALID_ASPECT_RATIOS = {"16:9", "9:16", "1:1", "4:3", "21:9"}
VALID_MEDIA_TYPES = {"image", "video"}

@router.post("")
async def upload_csv(file: UploadFile = File(...), token: str = Depends(get_token)):
    if not file.filename.lower().endswith('.csv'):
        raise HTTPException(status_code=400, detail="File must be a CSV file")
        
    contents = await file.read()
    
    # Detect encoding
    result = chardet.detect(contents)
    encoding = result['encoding'] or 'utf-8'
    
    try:
        text = contents.decode(encoding)
        reader = csv.DictReader(io.StringIO(text))
        
        # Check required columns
        fieldnames = [field.strip().lower() for field in (reader.fieldnames or [])]
        
        # Map common column name aliases
        column_mapping = {}
        for original_field in (reader.fieldnames or []):
            clean = original_field.strip().lower()
            if clean in ["id", "scene_id", "scene_number", "scene"]:
                column_mapping[original_field] = "id"
            elif clean in ["visual_prompt", "prompt", "image_prompt"]:
                column_mapping[original_field] = "visual_prompt"
            elif clean in ["voiceover_script", "script", "audio_script", "speech"]:
                column_mapping[original_field] = "voiceover_script"
            elif clean in ["filename", "output_filename", "name"]:
                column_mapping[original_field] = "filename"
            elif clean in ["aspect_ratio", "ratio"]:
                column_mapping[original_field] = "aspect_ratio"
            elif clean in ["media_type", "type"]:
                column_mapping[original_field] = "media_type"
            elif clean in ["voice_name", "voice"]:
                column_mapping[original_field] = "voice_name"
            else:
                column_mapping[original_field] = clean

        valid_rows: List[Dict[str, Any]] = []
        invalid_rows: List[Dict[str, Any]] = []
        seen_ids = set()
        
        for idx, row in enumerate(reader, start=1):
            # Normalize keys
            normalized_row = {}
            for k, v in row.items():
                target_key = column_mapping.get(k, k)
                normalized_row[target_key] = (v or "").strip()
                
            errors = []
            
            # 1. Check ID
            scene_id = normalized_row.get("id") or str(idx)
            normalized_row["id"] = scene_id
            if scene_id in seen_ids:
                errors.append(f"Duplicate scene ID '{scene_id}'")
            else:
                seen_ids.add(scene_id)
                
            # 2. Check visual prompt
            if not normalized_row.get("visual_prompt"):
                errors.append("Missing required visual_prompt")
                
            # 3. Check aspect ratio
            ar = normalized_row.get("aspect_ratio", "16:9")
            if ar and ar not in VALID_ASPECT_RATIOS:
                errors.append(f"Invalid aspect_ratio '{ar}'. Allowed: {', '.join(VALID_ASPECT_RATIOS)}")
            normalized_row["aspect_ratio"] = ar if ar in VALID_ASPECT_RATIOS else "16:9"
            
            # 4. Check media type
            mt = normalized_row.get("media_type", "image").lower()
            if mt and mt not in VALID_MEDIA_TYPES:
                errors.append(f"Invalid media_type '{mt}'. Allowed: {', '.join(VALID_MEDIA_TYPES)}")
            normalized_row["media_type"] = mt if mt in VALID_MEDIA_TYPES else "image"
            
            if errors:
                invalid_rows.append({
                    "row_number": idx,
                    "data": normalized_row,
                    "errors": errors
                })
            else:
                valid_rows.append(normalized_row)
                
        return {
            "filename": file.filename,
            "total_rows": len(valid_rows) + len(invalid_rows),
            "valid_count": len(valid_rows),
            "invalid_count": len(invalid_rows),
            "valid_rows": valid_rows,
            "invalid_rows": invalid_rows
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to parse CSV file: {str(e)}")
