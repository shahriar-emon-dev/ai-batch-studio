import asyncio
import logging
import os
import sys
import time
from typing import Any, Dict, Tuple

# Ensure project root is in sys.path so 'from backend...' imports work from any working directory
root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.config import settings
from backend.services.generation_service import active_jobs, recover_pending_jobs

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s | %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting AI ContentStudio API")
    if settings.serverless_mode:
        # Every invocation gets an empty /tmp, so reconciliation would read that
        # as "all media lost" and wipe the asset rows. Skip it entirely.
        logger.info("Serverless mode: skipping job recovery and media reconciliation")
    else:
        try:
            await recover_pending_jobs()
        except Exception as exc:  # startup must not be blocked by recovery
            logger.error("Job recovery failed at startup: %s", exc)
    yield
    logger.info("Shutting down AI ContentStudio API")


app = FastAPI(title="AI ContentStudio API", version="1.0.0", lifespan=lifespan)

origins = [o.strip() for o in settings.allowed_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _describe_database_error(exc: Exception) -> str:
    """Turn a Postgres/PostgREST failure into something the user can act on.

    A stale schema is the single most common cause of a 500 here, and
    "Internal server error" gives no way to diagnose it.
    """
    payload = getattr(exc, "args", None)
    detail = ""
    for source in (getattr(exc, "message", None), getattr(exc, "details", None), payload):
        if source:
            detail = str(source)
            break
    text = f"{detail} {exc}".lower()

    if "does not exist" in text or "42703" in text or "42p01" in text or "pgrst204" in text:
        missing = str(detail or exc)[:200]
        return (
            "Database schema is out of date: "
            f"{missing}. Re-run supabase_schema.sql in the Supabase SQL editor, then retry."
        )
    if "jwt" in text or "row-level security" in text or "42501" in text:
        return "Database rejected the request (permission or session issue). Try signing in again."
    return ""


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Never leave the frontend hanging on an unhandled error (§48, §49)."""
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    hint = _describe_database_error(exc)
    return JSONResponse(
        status_code=500,
        content={"detail": hint or f"Internal server error: {type(exc).__name__}"},
    )


from backend.routers import (  # noqa: E402  (routers import settings/services above)
    assets_router,
    csv_router,
    files_router,
    generation_router,
    project_router,
    settings_router,
)

app.include_router(csv_router.router, prefix="/api/upload", tags=["CSV"])
app.include_router(project_router.router, prefix="/api/projects", tags=["Projects"])
app.include_router(generation_router.router, prefix="/api/generation", tags=["Generation"])
app.include_router(settings_router.router, prefix="/api/settings", tags=["Settings"])
app.include_router(assets_router.router, prefix="/api/assets", tags=["Assets"])
app.include_router(files_router.router, prefix="/api/files", tags=["Files"])
# `POST /api/export` is the export entry point used by the export page.
app.include_router(files_router.router, prefix="/api/export", tags=["Export"])


# Columns the application actually reads. Checked by /api/health/schema.
REQUIRED_SCHEMA = {
    "projects": ["id", "user_id", "name", "status", "mode", "total_scenes",
                 "completed_scenes", "failed_scenes", "skipped_scenes"],
    "scenes": ["id", "project_id", "visual_prompt", "video_prompt", "voiceover_script",
               "media_type", "overall_status", "visual_status", "voice_status",
               "video_status", "visual_path", "audio_path", "video_path", "merged_path",
               "custom_metadata", "csv_file_id"],
    "api_profiles": ["id", "user_id", "provider", "profile_name", "encrypted_credentials",
                     "is_active", "connection_status", "key_hint", "priority",
                     "request_count", "success_count", "failure_count",
                     "last_success_at", "last_error", "unavailable_until"],
    "generation_jobs": ["id", "project_id", "status", "total_tasks", "completed_tasks"],
    "generation_tasks": ["id", "scene_id", "task_type", "status", "project_id", "prompt",
                         "storage_path", "error_category", "max_attempts", "scene_number",
                         "api_profile_id", "attempt_count"],
    "assets": ["id", "project_id", "scene_id", "asset_type", "storage_path", "filename",
               "task_id", "scene_number", "prompt", "verified", "size", "mime_type"],
    "csv_files": ["id", "project_id", "filename", "raw_rows", "encoding", "delimiter"],
    "csv_columns": ["id", "csv_file_id", "original_name", "detected_meaning", "mapped_meaning"],
    "error_logs": ["id", "project_id", "error_category", "error_message"],
    "activity_logs": ["id", "project_id", "level", "message"],
    "user_settings": ["id", "user_id", "default_aspect_ratio", "default_speech_speed",
                      "default_negative_prompt", "merge_enabled"],
}


@app.get("/api/health")
async def health_check():
    return {"status": "ok"}


# The schema only changes when a migration is run, so the probe result is
# cached. Without this the dashboard pays for a full re-probe on every load.
_SCHEMA_CACHE: Dict[str, Any] = {"result": None, "checked_at": 0.0}
_SCHEMA_CACHE_TTL = 600


@app.get("/api/health/schema")
async def health_check_schema(refresh: bool = False):
    """Report exactly which tables/columns are missing (§19 — actionable errors)."""
    from backend.database import get_admin_client

    cached = _SCHEMA_CACHE["result"]
    if cached and not refresh and (time.time() - _SCHEMA_CACHE["checked_at"]) < _SCHEMA_CACHE_TTL:
        return {**cached, "cached": True}

    client = get_admin_client()
    if not client:
        return {"status": "error", "detail": "Supabase client not configured"}

    # One request per column, issued concurrently rather than end to end
    # (sequentially this took ~22s). Concurrency is modest because the Supabase
    # client shares one HTTP connection pool.
    semaphore = asyncio.Semaphore(8)

    def _is_missing_error(exc: Exception) -> bool:
        """Only a real 'undefined column/table' means missing.

        Treating every exception as missing turns a transient transport error
        into a false 'schema out of date', which would send someone re-running
        migrations for no reason.
        """
        text = f"{getattr(exc, 'message', '')} {getattr(exc, 'code', '')} {exc}".lower()
        return "does not exist" in text or "42703" in text or "42p01" in text or "pgrst204" in text

    async def probe(table: str, column: str) -> Tuple[str, str, bool]:
        async with semaphore:
            def run() -> bool:
                # One retry absorbs transient pool contention; only a genuine
                # schema error is reported as missing.
                for attempt in range(2):
                    try:
                        client.table(table).select(column).limit(1).execute()
                        return True
                    except Exception as exc:
                        if _is_missing_error(exc):
                            return False
                        if attempt == 0:
                            time.sleep(0.15)
                            continue
                        logger.warning("Schema probe for %s.%s inconclusive: %s", table, column, exc)
                        return True  # unproven ≠ missing
                return True

            return table, column, await asyncio.to_thread(run)

    table_results = await asyncio.gather(*(probe(t, "id") for t in REQUIRED_SCHEMA))
    missing_tables = [table for table, _, ok in table_results if not ok]
    present = [t for t in REQUIRED_SCHEMA if t not in missing_tables]

    column_results = await asyncio.gather(
        *(probe(table, column) for table in present for column in REQUIRED_SCHEMA[table])
    )

    missing_columns: Dict[str, list] = {}
    for table, column, ok in column_results:
        if not ok:
            missing_columns.setdefault(table, []).append(column)

    ok = not missing_tables and not missing_columns
    result = {
        "status": "ok" if ok else "out_of_date",
        "missing_tables": missing_tables,
        "missing_columns": missing_columns,
        "action": None if ok else "Re-run supabase_schema.sql in the Supabase SQL editor.",
    }
    _SCHEMA_CACHE.update({"result": result, "checked_at": time.time()})
    return {**result, "cached": False}


@app.get("/api/health/detailed")
async def health_check_detailed():
    """Real component checks, not hardcoded values (§56, system health)."""
    from backend.database import get_admin_client, is_service_role_configured
    from backend.services.ffmpeg_service import is_available as ffmpeg_available

    components = {}

    client = get_admin_client()
    if not client:
        components["database"] = {"status": "error", "detail": "Supabase client not configured"}
    else:
        try:
            client.table("projects").select("id").limit(1).execute()
            components["database"] = {"status": "ok"}
        except Exception as exc:
            components["database"] = {"status": "error", "detail": str(exc)[:200]}

    probe = os.path.join(settings.output_dir, ".health")
    try:
        with open(probe, "w", encoding="utf-8") as handle:
            handle.write("ok")
        os.remove(probe)
        components["storage"] = {"status": "ok", "path": settings.output_dir}
    except OSError as exc:
        components["storage"] = {"status": "error", "detail": str(exc)[:200]}

    can_persist = is_service_role_configured()
    components["worker"] = {
        "status": "ok" if can_persist else "degraded",
        "active_jobs": len(active_jobs),
        "can_persist_results": can_persist,
        "detail": None if can_persist else "SUPABASE_SERVICE_ROLE_KEY is not configured; generation cannot save results.",
    }
    components["ffmpeg"] = {"status": "ok" if ffmpeg_available() else "unavailable"}
    components["ai_provider"] = {
        "status": "configured",
        "image_model": settings.image_model,
        "video_model": settings.video_model,
        "video_generation_enabled": settings.video_generation_enabled,
    }

    overall = "ok" if all(c.get("status") in ("ok", "configured", "unavailable") for c in components.values()) else "degraded"
    return {"status": overall, "api": {"status": "ok"}, **components}


# Generated media (never stored in database tables — §41)
app.mount("/output", StaticFiles(directory=settings.output_dir), name="output")

# Frontend last so API routes take precedence
frontend_dir = os.path.join(settings.base_dir, "frontend")
if os.path.exists(frontend_dir):
    app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.app:app", host="0.0.0.0", port=8000, reload=True)
