# Implementation Plan: Complete AI Batch Studio with Real Generation Pipeline

## Problem Statement

The current "serverless" architecture cannot support the core requirements:
- **Persistent background generation** (survives browser close)
- **Real file storage** on disk (images, audio, video)
- **FFmpeg video merging**
- **Large batch processing** (500+ scenes)
- **Secure API key usage** (keys never exposed to browser)

We must bring back a **Python/FastAPI backend** that handles the heavy AI generation work, while keeping **Supabase** for auth and database.

## Architecture

```
┌────────────┐     JWT Token     ┌─────────────┐    SQL + RLS    ┌──────────┐
│  Frontend   │ ◄──────────────► │   FastAPI    │ ◄────────────► │ Supabase │
│  (Static)   │    REST + SSE    │   Backend    │                │ Postgres │
└────────────┘                   └──────┬───────┘                └──────────┘
                                        │
                              ┌─────────┴──────────┐
                              │   Generation Queue  │
                              │   (asyncio workers) │
                              └─────────┬──────────┘
                                        │
                    ┌───────────────────┬┴──────────────────┐
                    ▼                   ▼                    ▼
            ┌──────────────┐  ┌────────────────┐  ┌──────────────┐
            │ Gemini API   │  │ Google Cloud   │  │   FFmpeg     │
            │ (Images)     │  │ TTS (Voice)    │  │ (Merge)      │
            └──────┬───────┘  └───────┬────────┘  └──────┬───────┘
                   │                  │                   │
                   └──────────────────┴───────────────────┘
                                      │
                              ┌───────▼───────┐
                              │  output/      │
                              │  ├── images/  │
                              │  ├── audio/   │
                              │  ├── videos/  │
                              │  └── merged/  │
                              └───────────────┘
```

## Proposed Changes

### Backend (Python/FastAPI) — New

---

#### [NEW] `backend/app.py`
Main FastAPI application with:
- Supabase JWT auth middleware (validates tokens from frontend)
- CORS config for frontend
- Lifespan handler (startup/shutdown)
- Static file serving for frontend
- Mounts all API routers

#### [NEW] `backend/config.py`
Pydantic Settings loading from `.env`:
- Supabase URL/Key
- Google API defaults (models, concurrency, retry)
- Output directory paths
- FFmpeg path detection

#### [NEW] `backend/auth.py`
JWT authentication middleware:
- Extracts `Authorization: Bearer <token>` from requests
- Validates JWT against Supabase's public JWKS endpoint
- Returns authenticated `user_id` (UUID)
- No local user database — Supabase handles users

#### [NEW] `backend/database.py`
Supabase client wrapper:
- Uses `supabase-py` library to connect to Supabase Postgres
- All queries filtered by `user_id` (multi-user isolation)
- CRUD for: projects, scenes, api_profiles, user_settings, activity_logs, generation_attempts

---

#### [NEW] `backend/routers/csv_router.py`
CSV upload and validation:
- `POST /api/upload/csv` — Accepts multipart CSV, validates columns, returns parsed rows
- Client-side CSV parsing removed; backend handles encoding detection (chardet)

#### [NEW] `backend/routers/project_router.py`
Project CRUD:
- `GET /api/projects` — List user's projects
- `POST /api/projects` — Create project from validated CSV rows
- `GET /api/projects/{id}` — Get project details + scene counts
- `DELETE /api/projects/{id}` — Delete project and associated scenes

#### [NEW] `backend/routers/generation_router.py`
Generation control:
- `POST /api/generation/start` — Start batch generation for a project
- `POST /api/generation/pause` — Pause active generation
- `POST /api/generation/resume` — Resume paused/interrupted generation
- `POST /api/generation/cancel` — Cancel generation (preserves completed work)
- `POST /api/generation/retry-failed` — Retry only failed scenes
- `GET /api/generation/events` — SSE endpoint for real-time progress

#### [NEW] `backend/routers/settings_router.py`
API key and settings management:
- `GET /api/settings` — Get user's settings
- `PUT /api/settings` — Update settings
- `POST /api/settings/api-key` — Save Google API key (encrypted in Supabase)
- `POST /api/settings/test-api` — Test Google API connection with saved key
- `GET /api/settings/voices` — List available TTS voices from Google

#### [NEW] `backend/routers/files_router.py`
File management and export:
- `GET /api/files/{project_id}` — List generated files for a project
- `GET /api/files/download/{scene_id}/{type}` — Download individual file
- `POST /api/files/export` — Create ZIP export of selected files
- `GET /api/files/export/{export_id}` — Download generated ZIP

---

#### [NEW] `backend/services/generation_service.py`
Core generation engine:
- Asyncio-based background worker queue
- Processes scenes with configurable concurrency
- For each scene:
  1. Read visual_prompt → Call Gemini `generateContent` with `responseModalities: ["IMAGE"]` → Save PNG
  2. Read voiceover_script → Call Google Cloud TTS `text:synthesize` → Save WAV/MP3
  3. (Optional) FFmpeg merge image+audio → Save MP4
- Updates scene status in Supabase after each step
- Broadcasts progress via SSE
- Handles pause/resume/cancel
- Automatic retry with exponential backoff
- Quota error detection and graceful pause

#### [NEW] `backend/services/google_ai_service.py`
Google API client:
- `generate_image(api_key, prompt, aspect_ratio)` — Calls Gemini `generateContent` with IMAGE modality
- `generate_video(api_key, prompt, aspect_ratio)` — Calls Veo model (if available)
- `generate_speech(api_key, text, voice, language, speed)` — Calls Google Cloud TTS
- `list_voices(api_key, language)` — Lists available TTS voices
- `test_connection(api_key)` — Validates API key works
- Proper error handling for quota/billing/rate limits

#### [NEW] `backend/services/ffmpeg_service.py`
Video merging:
- Detects FFmpeg installation
- `merge_image_audio(image_path, audio_path, output_path)` — Creates MP4 from still image + audio
- `merge_video_audio(video_path, audio_path, output_path)` — Combines video + audio track

#### [NEW] `backend/services/export_service.py`
Export functionality:
- Creates ZIP archives from selected files
- Supports organized folder structures (by type or by scene)
- Cleanup of temporary ZIP files

---

### Frontend — Modifications

#### [MODIFY] [index.html](file:///e:/video%20automation/ai_batch_studio/frontend/index.html)
- Remove auth guard script (backend handles auth)
- Keep Supabase CDN for login token management

#### [MODIFY] [app.js](file:///e:/video%20automation/ai_batch_studio/frontend/static/js/app.js)
- Replace `DB.*` Supabase direct calls → `API.get/post()` calls to backend
- Restore SSE connection for real-time progress
- Add file browser view
- Add export functionality
- CSV upload sends file to backend instead of parsing client-side

#### [MODIFY] [dashboard.html](file:///e:/video%20automation/ai_batch_studio/frontend/dashboard.html)
- Fetch stats from backend API instead of Supabase direct

#### [MODIFY] [config.js](file:///e:/video%20automation/ai_batch_studio/frontend/config.js)
- Add `window.API_BASE_URL` pointing to the backend (default `http://localhost:8000`)

---

### Configuration & Schema

#### [MODIFY] [.env](file:///e:/video%20automation/ai_batch_studio/.env)
- Add `SUPABASE_SERVICE_ROLE_KEY` (needed for backend to bypass RLS when updating generation status)

#### [EXISTING] [supabase_schema.sql](file:///e:/video%20automation/ai_batch_studio/supabase_schema.sql)
- Already has the correct schema with RLS. No changes needed if already run.

#### [NEW] `requirements.txt`
```
fastapi>=0.115.0
uvicorn[standard]>=0.30.0
python-multipart>=0.0.9
jinja2>=3.1.4
aiofiles>=24.1.0
supabase>=2.7.4
pyjwt[crypto]>=2.8.0
httpx>=0.27.0
python-dotenv>=1.0.1
pydantic>=2.7.0
pydantic-settings>=2.3.0
cryptography>=43.0.0
chardet>=5.2.0
```

---

## User Review Required

> [!IMPORTANT]
> This plan brings back the Python backend you previously chose to delete. The backend is **required** for persistent background generation, secure API key handling, FFmpeg, and file management. The frontend remains a static site served by the backend.

> [!WARNING]
> You will need to add your **Supabase Service Role Key** (found in Supabase Dashboard → Settings → API → `service_role` key) to your `.env` file. This key lets the backend write to the database on behalf of users during generation.

## Open Questions

1. **Have you already run the `supabase_schema.sql`** in your Supabase SQL Editor? If not, you'll need to do that first.
2. **Do you have FFmpeg installed?** If not, merge features will be disabled (but images + audio will still work).
3. **Vercel deployment**: The frontend will work on Vercel for auth pages, but the main app (`index.html`) needs to connect to your backend server. For now, you'll run the backend locally with `python app.py`. We can discuss cloud hosting later.

## Verification Plan

### End-to-End Test
1. Start backend: `cd backend && python app.py`
2. Open `http://localhost:8000/login.html`
3. Register → Login → Save API key → Test connection
4. Upload the sample CSV (2-3 scenes)
5. Start generation → Watch real-time progress
6. Verify actual files appear in `output/images/` and `output/audio/`
7. Select files → Export as ZIP
8. Close browser → Reopen → Verify job state persists
9. Retry any failed scenes
