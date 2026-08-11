import sys
import os

# Ensure project root is in sys.path so 'from backend...' imports work from any working directory
root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.config import settings

from contextlib import asynccontextmanager
from backend.services.generation_service import recover_pending_jobs
import logging

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting AI Content Studio...")
    await recover_pending_jobs()
    yield
    # Shutdown
    logger.info("Shutting down...")

app = FastAPI(title="AI Batch Studio API", lifespan=lifespan)

# Configure CORS
origins = [o.strip() for o in settings.allowed_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from backend.routers import csv_router, project_router, generation_router, settings_router, files_router
app.include_router(csv_router.router, prefix="/api/upload", tags=["Upload"])
app.include_router(project_router.router, prefix="/api/projects", tags=["Projects"])
app.include_router(generation_router.router, prefix="/api/generation", tags=["Generation"])
app.include_router(settings_router.router, prefix="/api/settings", tags=["Settings"])
app.include_router(files_router.router, prefix="/api/files", tags=["Files"])
app.include_router(files_router.router, prefix="/api/export", tags=["Export"])

@app.get("/api/health")
async def health_check():
    return {"status": "ok"}

@app.get("/api/health/detailed")
async def health_check_detailed():
    from backend.database import admin_client
    
    health = {
        "api": "ok",
        "database": "ok" if admin_client else "error",
        "storage": "ok" if os.path.exists(settings.output_dir) else "error",
        "worker": "ok"
    }
    return health

# Mount output directory for file access
app.mount("/output", StaticFiles(directory=settings.output_dir), name="output")

# Mount frontend static files last so API routes take precedence
frontend_dir = os.path.join(settings.base_dir, "frontend")
if os.path.exists(frontend_dir):
    app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.app:app", host="0.0.0.0", port=8000, reload=True)
