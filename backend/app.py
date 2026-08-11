from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import os

from backend.config import settings

app = FastAPI(title="AI Batch Studio API")

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify domains
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

@app.get("/api/health")
async def health_check():
    return {"status": "ok"}

# Mount output directory for file access
app.mount("/output", StaticFiles(directory=settings.output_dir), name="output")

# Mount frontend static files last so API routes take precedence
frontend_dir = os.path.join(settings.base_dir, "frontend")
if os.path.exists(frontend_dir):
    app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.app:app", host="0.0.0.0", port=8000, reload=True)
