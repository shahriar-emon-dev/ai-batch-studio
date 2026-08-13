"""CSV upload, analysis, mapping and preview API (proposal §14–§22)."""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel

from backend.auth import get_token, verify_token
from backend.config import settings
from backend.database import get_db_client
from backend.services import csv_service

logger = logging.getLogger(__name__)
router = APIRouter()

MAX_UPLOAD_BYTES = settings.max_upload_size_mb * 1024 * 1024


class MappingRequest(BaseModel):
    mapping: Dict[str, str]


def _persist_analysis(client, user_id: str, project_id: int, analysis: Dict[str, Any]) -> Optional[int]:
    """Store csv_files + csv_columns so the CSV stays inspectable later (§20, §21)."""
    try:
        file_row = (
            client.table("csv_files")
            .insert(
                {
                    "project_id": project_id,
                    "user_id": user_id,
                    "filename": analysis["filename"],
                    "file_size_bytes": analysis["file_size_bytes"],
                    "encoding": analysis["encoding"],
                    "delimiter": analysis["delimiter"],
                    "row_count": analysis["total_rows"],
                    "column_count": analysis["columns_count"],
                    "valid_row_count": analysis["valid_count"],
                    "invalid_row_count": analysis["invalid_count"],
                    "has_master_prompt": analysis["has_master_prompt"],
                    "status": "ANALYZED",
                    "raw_rows": analysis["raw_rows"],
                }
            )
            .execute()
        )
        if not file_row.data:
            return None

        csv_file_id = file_row.data[0]["id"]
        client.table("csv_columns").insert(
            [
                {
                    "csv_file_id": csv_file_id,
                    "user_id": user_id,
                    "column_index": column["column_index"],
                    "original_name": column["original_name"],
                    "detected_meaning": column["detected_meaning"],
                    "mapped_meaning": column["detected_meaning"],
                    "confidence": column["confidence"],
                    "data_type": column["data_type"],
                    "non_empty_count": column["non_empty_count"],
                    "example_value": column["example_value"],
                }
                for column in analysis["detected_columns"]
            ]
        ).execute()
        return csv_file_id
    except Exception as exc:
        logger.error("Could not persist CSV analysis: %s", exc)
        return None


@router.post("")
@router.post("/csv")
async def upload_csv(
    file: UploadFile = File(...),
    project_id: Optional[int] = Query(None),
    token: str = Depends(get_token),
    user_id: str = Depends(verify_token),
):
    """Analyze an uploaded CSV; persist it when a project is supplied."""
    if not (file.filename or "").lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="File must have a .csv extension")

    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=400, detail="The uploaded file is empty")
    if len(contents) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds the {settings.max_upload_size_mb} MB upload limit",
        )

    try:
        analysis = csv_service.analyze(contents, file.filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("CSV analysis failed")
        raise HTTPException(status_code=400, detail=f"Failed to parse CSV file: {exc}")

    csv_file_id = None
    if project_id is not None:
        client = get_db_client(token)
        if not client.table("projects").select("id").eq("id", project_id).execute().data:
            raise HTTPException(status_code=404, detail="Project not found")
        csv_file_id = _persist_analysis(client, user_id, project_id, analysis)

    analysis["csv_file_id"] = csv_file_id
    analysis["available_mappings"] = csv_service.CANONICAL_FIELDS
    return analysis


@router.get("/csv/{csv_file_id}")
async def get_csv_detail(csv_file_id: int, token: str = Depends(get_token), user_id: str = Depends(verify_token)):
    """CSV detail view: name, size, upload date, rows, columns, encoding (§20)."""
    client = get_db_client(token)
    result = (
        client.table("csv_files")
        .select("id, project_id, filename, file_size_bytes, encoding, delimiter, row_count, column_count, valid_row_count, invalid_row_count, has_master_prompt, status, created_at")
        .eq("id", csv_file_id)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="CSV file not found")
    return result.data[0]


@router.get("/csv/{csv_file_id}/columns")
async def get_csv_columns(csv_file_id: int, token: str = Depends(get_token), user_id: str = Depends(verify_token)):
    """Column view: detected meaning, type, confidence, counts, example (§21)."""
    client = get_db_client(token)
    if not client.table("csv_files").select("id").eq("id", csv_file_id).execute().data:
        raise HTTPException(status_code=404, detail="CSV file not found")

    columns = (
        client.table("csv_columns")
        .select("*")
        .eq("csv_file_id", csv_file_id)
        .order("column_index")
        .execute()
        .data
        or []
    )
    return {"columns": columns, "available_mappings": csv_service.CANONICAL_FIELDS}


# Upper bound on rows returned in one response. Large enough that a normal
# batch CSV is delivered whole for a single scrollable table (§22); bounded so
# a 100k-row file cannot lock up the browser.
MAX_PREVIEW_ROWS = 5000


@router.get("/csv/{csv_file_id}/rows")
async def get_csv_rows(
    csv_file_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(MAX_PREVIEW_ROWS, ge=1, le=MAX_PREVIEW_ROWS),
    search: str = "",
    sort_by: str = "",
    order: str = "asc",
    token: str = Depends(get_token), user_id: str = Depends(verify_token),
):
    """Searchable, sortable preview of exactly what was imported (§22).

    Returns the whole file by default so the UI can present one continuously
    scrollable table instead of making the user click through pages.
    """
    client = get_db_client(token)
    result = client.table("csv_files").select("raw_rows").eq("id", csv_file_id).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="CSV file not found")

    rows: List[Dict[str, Any]] = result.data[0].get("raw_rows") or []

    # Union of keys, so a row with an overflow column does not hide it.
    headers: List[str] = []
    for row in rows:
        for key in row:
            if key not in headers:
                headers.append(key)

    total_all = len(rows)

    if search:
        needle = search.lower()
        rows = [r for r in rows if any(needle in str(v).lower() for v in r.values())]

    if sort_by and sort_by in headers:
        rows = sorted(rows, key=lambda r: str(r.get(sort_by) or "").lower(), reverse=(order == "desc"))

    total = len(rows)
    start = (page - 1) * page_size
    window = rows[start : start + page_size]

    return {
        "headers": headers,
        "rows": window,
        "total": total,
        "total_all": total_all,
        "returned": len(window),
        "truncated": total > len(window),
        "page": page,
        "page_size": page_size,
        "total_pages": max(1, (total + page_size - 1) // page_size),
    }


@router.post("/csv/{csv_file_id}/mapping")
async def apply_mapping(
    csv_file_id: int,
    req: MappingRequest,
    token: str = Depends(get_token),
    user_id: str = Depends(verify_token),
):
    """Re-normalize the stored CSV with the user's manual mapping (§18).

    Normalization stays on the server so the browser and the pipeline can never
    disagree about what a column means.
    """
    client = get_db_client(token)
    result = client.table("csv_files").select("raw_rows").eq("id", csv_file_id).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="CSV file not found")

    raw_rows = result.data[0].get("raw_rows") or []
    if not raw_rows:
        raise HTTPException(status_code=400, detail="This CSV has no stored rows to remap")

    unknown = [field for field in req.mapping.values() if field not in csv_service.CANONICAL_FIELDS]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown mapping target(s): {', '.join(sorted(set(unknown)))}")

    # Two columns pointing at one scene field would mean one column's data is
    # thrown away, so refuse instead of losing it silently (§19).
    duplicates = csv_service.duplicate_targets(req.mapping)
    if duplicates:
        detail = "; ".join(
            f"'{field}' is assigned to {', '.join(columns)}" for field, columns in duplicates.items()
        )
        raise HTTPException(
            status_code=400,
            detail=f"Each field can only be mapped once. {detail}. Map the extra column(s) to Custom Metadata or Ignore.",
        )

    normalized = csv_service.normalize_rows(raw_rows, req.mapping)

    try:
        for header, target in req.mapping.items():
            client.table("csv_columns").update(
                {"mapped_meaning": target, "is_manual_override": True}
            ).eq("csv_file_id", csv_file_id).eq("original_name", header).execute()

        client.table("csv_files").update(
            {
                "valid_row_count": normalized["valid_count"],
                "invalid_row_count": normalized["invalid_count"],
                "has_master_prompt": normalized["has_master_prompt"],
                "status": "MAPPED",
            }
        ).eq("id", csv_file_id).execute()
    except Exception as exc:
        logger.error("Could not persist mapping overrides: %s", exc)

    return {"csv_file_id": csv_file_id, "mapping": req.mapping, **normalized}
