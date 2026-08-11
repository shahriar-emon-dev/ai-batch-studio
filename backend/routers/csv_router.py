import csv
import io
import chardet
from fastapi import APIRouter, UploadFile, File, HTTPException, Depends
from backend.auth import get_current_user

router = APIRouter()

@router.post("")
async def upload_csv(file: UploadFile = File(...), user_id: str = Depends(get_current_user)):
    if not file.filename.endswith('.csv'):
        raise HTTPException(status_code=400, detail="File must be a CSV")
        
    contents = await file.read()
    
    # Detect encoding
    result = chardet.detect(contents)
    encoding = result['encoding'] or 'utf-8'
    
    try:
        text = contents.decode(encoding)
        # Parse CSV
        reader = csv.DictReader(io.StringIO(text))
        rows = [row for row in reader]
        
        return {"filename": file.filename, "rows": rows}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to parse CSV: {str(e)}")
