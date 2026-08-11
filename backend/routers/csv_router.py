import csv
import io
import chardet
from typing import List, Dict, Any, Tuple
from fastapi import APIRouter, UploadFile, File, HTTPException, Depends
from backend.auth import get_token

router = APIRouter()

VALID_ASPECT_RATIOS = {"16:9", "9:16", "1:1", "4:3", "21:9"}
VALID_MEDIA_TYPES = {"image", "voice", "video", "image_voice", "video_voice"}

COLUMN_CANONICAL_MAP = {
    "id": ["id", "scene_id", "scene_number", "scene", "number", "#"],
    "visual_prompt": ["visual_prompt", "image_prompt", "visual description", "scene description", "prompt", "image description", "visual_description", "visual", "concept", "description"],
    "voiceover_script": ["voiceover_script", "voiceover", "narration", "narration_script", "voice_script", "audio_script", "script", "speech", "narration_text", "audio_text", "speech_text"],
    "aspect_ratio": ["aspect_ratio", "ratio", "image_ratio", "video_ratio", "format", "dimension", "aspect"],
    "duration": ["duration", "length", "time", "sec", "seconds"],
    "master_prompt": ["master_prompt", "master", "global_prompt", "style_prompt", "master_style"],
    "style": ["style", "art_style", "visual_style", "genre"],
    "negative_prompt": ["negative_prompt", "negative", "avoid", "exclude"],
    "tone": ["tone", "voice_tone", "emotion", "mood"],
    "voice_name": ["voice_name", "voice", "voice_style", "speaker"],
    "media_type": ["media_type", "type", "generation_type", "output_type"],
    "filename": ["filename", "output_filename", "name", "file"]
}

def classify_column(original_header: str) -> Tuple[str, int]:
    """
    Intelligently classifies a CSV header into a target schema field with a confidence score.
    """
    clean = original_header.strip().lower().replace("-", "_").replace(" ", "_")
    
    # Exact match check
    for canonical_name, aliases in COLUMN_CANONICAL_MAP.items():
        if clean in aliases:
            confidence = 99 if clean == canonical_name else 95
            return canonical_name, confidence
            
    # Fuzzy substring match check
    for canonical_name, aliases in COLUMN_CANONICAL_MAP.items():
        for alias in aliases:
            if len(alias) > 3 and (alias in clean or clean in alias):
                return canonical_name, 88
                
    # Default to custom metadata
    return "custom_metadata", 80

@router.post("")
async def upload_csv(file: UploadFile = File(...), token: str = Depends(get_token)):
    if not file.filename.lower().endswith('.csv'):
        raise HTTPException(status_code=400, detail="File must be a CSV file")
        
    contents = await file.read()
    
    # 1. Detect Encoding
    result = chardet.detect(contents)
    encoding = result['encoding'] or 'utf-8'
    
    try:
        text = contents.decode(encoding)
        
        # 2. Detect Delimiter
        sample = text[:2048]
        delimiter = ','
        if ';' in sample and sample.count(';') > sample.count(','):
            delimiter = ';'
        elif '\t' in sample and sample.count('\t') > sample.count(','):
            delimiter = '\t'
        elif '|' in sample and sample.count('|') > sample.count(','):
            delimiter = '|'
            
        reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
        raw_headers = reader.fieldnames or []
        
        # 3. Classify Columns with Confidence Scores
        column_analysis: List[Dict[str, Any]] = []
        mapping_dict: Dict[str, str] = {}
        
        has_images = False
        has_voiceovers = False
        has_videos = False
        has_master = False
        
        for header in raw_headers:
            target_field, confidence = classify_column(header)
            column_analysis.append({
                "original_name": header,
                "detected_meaning": target_field,
                "confidence": confidence
            })
            mapping_dict[header] = target_field
            
            if target_field == "visual_prompt": has_images = True
            if target_field == "voiceover_script": has_voiceovers = True
            if target_field == "duration" or "video" in header.lower(): has_videos = True
            if target_field == "master_prompt": has_master = True

        valid_rows: List[Dict[str, Any]] = []
        invalid_rows: List[Dict[str, Any]] = []
        seen_ids = set()
        
        for idx, row in enumerate(reader, start=1):
            normalized_row: Dict[str, Any] = {
                "custom_metadata": {}
            }
            
            # Map canonical fields & preserve unmapped columns in custom_metadata
            for original_header, val in row.items():
                val_str = (val or "").strip()
                target_field = mapping_dict.get(original_header, "custom_metadata")
                
                if target_field == "custom_metadata":
                    normalized_row["custom_metadata"][original_header] = val_str
                else:
                    normalized_row[target_field] = val_str
                    
            errors = []
            
            # Ensure ID
            scene_id = normalized_row.get("id") or str(idx)
            normalized_row["id"] = scene_id
            if scene_id in seen_ids:
                errors.append(f"Duplicate scene ID '{scene_id}'")
            else:
                seen_ids.add(scene_id)
                
            # Aspect ratio normalization
            ar = normalized_row.get("aspect_ratio", "16:9")
            if ar and ar not in VALID_ASPECT_RATIOS:
                errors.append(f"Invalid aspect_ratio '{ar}'. Defaulting to 16:9.")
                normalized_row["aspect_ratio"] = "16:9"
            else:
                normalized_row["aspect_ratio"] = ar or "16:9"
                
            # Inferred media type if not provided
            mt = normalized_row.get("media_type")
            if not mt:
                if normalized_row.get("visual_prompt") and normalized_row.get("voiceover_script"):
                    mt = "image_voice"
                elif normalized_row.get("visual_prompt"):
                    mt = "image"
                elif normalized_row.get("voiceover_script"):
                    mt = "voice"
                else:
                    mt = "image"
            normalized_row["media_type"] = mt

            # Require at least visual_prompt or voiceover_script
            if not normalized_row.get("visual_prompt") and not normalized_row.get("voiceover_script"):
                errors.append("Row contains neither visual prompt nor voiceover script")

            if errors and not normalized_row.get("visual_prompt") and not normalized_row.get("voiceover_script"):
                invalid_rows.append({"row_number": idx, "data": normalized_row, "errors": errors})
            else:
                valid_rows.append(normalized_row)

        return {
            "filename": file.filename,
            "file_size_bytes": len(contents),
            "total_rows": len(valid_rows) + len(invalid_rows),
            "columns_count": len(raw_headers),
            "encoding": encoding,
            "delimiter": delimiter,
            "detected_columns": column_analysis,
            "detected_media_requirements": {
                "has_images": has_images,
                "has_voiceovers": has_voiceovers,
                "has_videos": has_videos
            },
            "has_master_prompt": has_master,
            "valid_count": len(valid_rows),
            "invalid_count": len(invalid_rows),
            "valid_rows": valid_rows,
            "invalid_rows": invalid_rows
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to parse CSV file: {str(e)}")
