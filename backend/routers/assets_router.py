"""Asset browser API (proposal §42) — search, filter, sort, paginate."""

import logging
import os
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from backend.auth import get_token, verify_token
from backend.database import get_db_client
from backend.services.asset_service import local_path_for_url

logger = logging.getLogger(__name__)
router = APIRouter()

SORTABLE = {"created_at", "filename", "scene_id", "size", "asset_type"}


def _safe_search(term: str) -> str:
    """Strip characters that would break PostgREST's `or=(...)` grammar.

    Commas separate conditions and parentheses delimit the group, so an
    unescaped one turns a search into a malformed filter and a 500.
    """
    return re.sub(r'[,()"\\*]', " ", term or "").strip()


@router.get("")
async def list_assets(
    project_id: Optional[int] = None,
    scene_id: Optional[int] = None,
    asset_type: Optional[str] = None,
    search: str = "",
    sort_by: str = "created_at",
    order: str = "desc",
    page: int = Query(1, ge=1),
    page_size: int = Query(24, ge=1, le=200),
    token: str = Depends(get_token),
    user_id: str = Depends(verify_token),
):
    client = get_db_client(token)

    def base_query(select: str):
        query = client.table("assets").select(select, count="exact").eq("user_id", user_id)
        if project_id:
            query = query.eq("project_id", project_id)
        if scene_id:
            query = query.eq("scene_id", scene_id)
        if asset_type and asset_type != "all":
            if asset_type == "video":
                query = query.in_("asset_type", ["video", "merged"])
            else:
                query = query.eq("asset_type", asset_type)
        term = _safe_search(search)
        if term:
            query = query.or_(
                f"filename.ilike.%{term}%,prompt.ilike.%{term}%,scene_number.ilike.%{term}%"
            )
        return query

    column = sort_by if sort_by in SORTABLE else "created_at"
    start = (page - 1) * page_size

    result = (
        base_query("*")
        .order(column, desc=(order.lower() == "desc"))
        .range(start, start + page_size - 1)
        .execute()
    )

    total = result.count if result.count is not None else len(result.data or [])
    return {
        "assets": result.data or [],
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": max(1, (total + page_size - 1) // page_size),
    }


@router.get("/summary")
async def asset_summary(
    project_id: Optional[int] = None,
    token: str = Depends(get_token),
    user_id: str = Depends(verify_token),
):
    """Counts per asset type, used by the browser's filter chips."""
    client = get_db_client(token)
    query = client.table("assets").select("asset_type, size").eq("user_id", user_id)
    if project_id:
        query = query.eq("project_id", project_id)
    rows = query.execute().data or []

    return {
        "total": len(rows),
        "images": sum(1 for r in rows if r.get("asset_type") == "image"),
        "voiceovers": sum(1 for r in rows if r.get("asset_type") == "voiceover"),
        "videos": sum(1 for r in rows if r.get("asset_type") in ("video", "merged")),
        "total_bytes": sum(r.get("size") or 0 for r in rows),
    }


@router.get("/{asset_id}/download")
async def download_asset(asset_id: int, token: str = Depends(get_token), user_id: str = Depends(verify_token)):
    client = get_db_client(token)
    result = client.table("assets").select("*").eq("id", asset_id).eq("user_id", user_id).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Asset not found")

    asset = result.data[0]
    path = local_path_for_url(asset.get("storage_path"))
    if not path or not os.path.exists(path):
        raise HTTPException(status_code=410, detail="The stored file for this asset is no longer available")

    return FileResponse(
        path,
        media_type=asset.get("mime_type") or "application/octet-stream",
        filename=asset.get("filename") or os.path.basename(path),
    )


@router.delete("/{asset_id}")
async def delete_asset(asset_id: int, token: str = Depends(get_token), user_id: str = Depends(verify_token)):
    client = get_db_client(token)
    result = client.table("assets").select("*").eq("id", asset_id).eq("user_id", user_id).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="Asset not found")

    path = local_path_for_url(result.data[0].get("storage_path"))
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except OSError as exc:
            logger.warning("Could not delete %s: %s", path, exc)

    client.table("assets").delete().eq("id", asset_id).execute()
    return {"status": "deleted", "id": asset_id}
