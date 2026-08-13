# AI ContentStudio

Intelligent CSV-driven AI image, voiceover and video batch generation.

Upload a spreadsheet of scenes, let the system work out what each column means,
then generate images, voiceovers and videos in the background — with real task
tracking, retry, API-key rotation, live progress and selective export.

---

## How it works

```
CSV upload → analysis → column mapping → scenes
                                          ↓
                       generation_tasks (one per scene × asset type)
                                          ↓
        worker pool → Google AI → media storage → assets table → live progress
```

The backend is the source of truth. Every number the UI shows is computed from
`generation_tasks` and `assets` rows, so a browser refresh always reproduces the
real state, and a completion mark only ever appears for an asset whose bytes are
verifiably on disk.

---

## Stack

| Layer | Technology |
|---|---|
| Frontend | Static multi-page app (vanilla JS), Supabase JS for auth + realtime |
| API | FastAPI |
| Database | Supabase Postgres with Row Level Security |
| Auth | Supabase Auth (email + password) |
| Generation | Google AI — Gemini image, Cloud/Gemini TTS, Veo video |
| Media storage | Local/mounted volume served at `/output` (never in database tables) |
| Merging | FFmpeg (optional) |

---

## Setup

### 1. Prerequisites

- Python 3.10+
- A Supabase project
- A Google AI Studio API key
- FFmpeg on `PATH` (optional — enables image + voiceover → MP4 merging)

### 2. Install

```bash
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # macOS / Linux
pip install -r requirements.txt
```

### 3. Create the database schema

Open Supabase → **SQL Editor**, paste the whole of [`supabase_schema.sql`](supabase_schema.sql)
and run it. It is idempotent, so re-running it after an update is safe. It creates
the tables, RLS policies, indexes and the realtime publication.

> Existing installations: the file has a **Part 2** section that adds the CSV
> ingestion tables, task/asset columns and profile-health columns. Re-run the
> whole file to pick them up.

### 4. Configure `.env`

```bash
copy .env.example .env
```

Three settings are not optional:

| Variable | Why it is required |
|---|---|
| `SUPABASE_URL` + `SUPABASE_KEY` | API and auth |
| `SUPABASE_SERVICE_ROLE_KEY` | The background worker has no user request to borrow credentials from. Without it, RLS rejects every write and **generation cannot save results** — the API refuses to start a job and says so. |
| `ENCRYPTION_KEY` | Encrypts stored Google API keys. Must be ≥ 32 characters. Generate with `python -c "import secrets;print(secrets.token_urlsafe(32))"` |

### 5. Run

```bash
python -m backend.app
# or: uvicorn backend.app:app --reload
```

Open **http://localhost:8000** (not `0.0.0.0`). Interactive API docs at `/docs`.

### 6. First run

1. Register, then sign in.
2. **Settings → Google AI Profiles** → add a key → **Test** (a real API call; the
   exact provider response is displayed, never a fake success).
3. **Projects → New Project → Workspace** → upload a CSV.
4. Review the detected column mapping, adjust anything flagged low-confidence,
   then **Apply Mapping & Import Scenes**.
5. **Start Batch Generation** → watch **Batch Generation** for live progress.
6. **Asset Browser** to preview, **Selective Export** to download.

---

## CSV handling

No fixed schema is required. Encoding, delimiter and header row are detected,
each column is classified by name *and* by its values, and a confidence score is
shown. Anything below 80% is flagged for review, and every mapping can be
overridden — re-normalization then happens on the server, so the mapping shown
is exactly the mapping that generates.

**Recognised targets:** scene number, visual prompt, video prompt, voiceover
script, master prompt, negative prompt, style, tone, voice, language, duration,
aspect ratio, media type, filename, character, camera, lighting.

**Unknown columns are never dropped** — they are preserved in `custom_metadata`,
and recognised descriptive ones (character, camera, lighting, location, mood,
time of day, …) are folded into the composed prompt.

```csv
id,visual_prompt,voiceover_script,aspect_ratio,filename
001,"Cinematic aerial view of ancient Rome at golden hour","Imagine standing above Rome two thousand years ago.","16:9","scene_001"
```

Equally valid — different headers, extra columns, semicolon-delimited:

```csv
Scene No;Image Description;Narration Text;Ratio;Mood;Camera Angle;Historical Period
1;A vast desert at dawn;The sands remember every footstep.;9:16;Serene;Low angle wide;Bronze Age
```

### What gets generated

`media_type` decides; when it is absent the requirement is inferred from which
prompt columns actually have content.

| media_type | Tasks created |
|---|---|
| `image` | image |
| `voice` | voiceover |
| `video` | video |
| `image_voice` | image + voiceover (+ merge) |
| `video_voice` | video + voiceover |
| *(absent)* | inferred from the populated prompt columns |

---

## Generation behaviour

- **One task per asset.** `generation_tasks` records the prompt sent, the API
  profile used, attempt count, timings, storage reference and any error — so
  every output is traceable back to its inputs.
- **Idempotent.** Re-running a job skips assets already generated and verified;
  re-importing a CSV replaces the scene set rather than duplicating it.
- **Retry with backoff.** Retryable failures (network, timeout, 5xx, rate limit)
  back off exponentially. Permanent failures (invalid key, rejected prompt) are
  not retried.
- **Profile rotation.** When a key reports exhausted quota or a rate limit, it is
  taken out of rotation for a cooldown and the next configured key is used. This
  only rotates between credentials you configured; it never circumvents provider
  limits.
- **Unsupported ≠ failed.** If Veo is not enabled for your account, or FFmpeg is
  not installed, those tasks are recorded `UNSUPPORTED` — never reported as
  generated.
- **Restart-safe.** Tasks interrupted by a restart are reopened for retry;
  completed work is untouched.

### Statuses

`PENDING → QUEUED → PROCESSING → COMPLETED | FAILED → RETRYING`,
plus `CANCELLED`, `SKIPPED` and `UNSUPPORTED`.

---

## Video generation

Off by default. Veo requires a paid, allow-listed key, so video tasks are
recorded as `UNSUPPORTED` until you opt in:

```env
VIDEO_GENERATION_ENABLED=true
DEFAULT_VIDEO_MODEL=veo-3.1-fast-generate-preview
```

Independently, when a scene produces both an image and a voiceover and FFmpeg is
available, the two are merged into an MP4 per scene.

---

## Voiceovers

Google Cloud Text-to-Speech is used when the key has it enabled, giving named
voices and speaking-rate control. If it is not enabled, the system falls back to
the Gemini TTS model automatically, so voiceovers still work with a plain AI
Studio key. Per-scene `voice`, `language` and `speaking_speed` override the
defaults set in Settings.

---

## Output layout

```
output/
├── images/       # <scene_id>.png
├── audio/        # <scene_id>.mp3 | .wav
├── videos/       # <scene_id>.mp4   (Veo)
└── merged/       # <scene_id>.mp4   (image + voiceover)
```

Storage keys are scene-id based so re-runs overwrite in place. Exports rename
files to the friendly `filename` from your CSV and organise them by asset type
or by scene, with a `metadata/manifest.json` and `metadata/scenes.csv`.

---

## Security

- API keys are encrypted server-side; only a masked hint (`••••••••••••abcd`)
  is ever returned to the browser.
- The browser never calls Google directly and never holds a key.
- Session tokens are verified — locally via `SUPABASE_JWT_SECRET` or the project
  JWKS, otherwise against Supabase itself. Unverified tokens are rejected.
- Every user-facing table is protected by RLS keyed on `auth.uid()`.
- Uploads are extension- and size-checked; stored media paths are constrained to
  the media directory.
- Media filenames carry an HMAC token derived from `ENCRYPTION_KEY`
  (`/output/images/42_9f3c….png`). Because generated media is served statically
  so `<img src>` works, a sequential name like `42.png` would let anyone
  enumerate another account's assets. The token is stable, so regeneration still
  overwrites in place. Media generated before this change still resolves under
  its old name.

---

## Project layout

```
backend/
├── app.py                  # FastAPI app, health checks, static mounts
├── config.py               # Settings (.env)
├── auth.py                 # Token verification + key encryption
├── database.py             # Supabase clients (user-scoped and worker)
├── routers/
│   ├── csv_router.py       # Upload, analysis, mapping, preview
│   ├── project_router.py   # Projects, scenes, dashboard stats
│   ├── generation_router.py# Start/pause/resume/cancel/retry, progress, logs
│   ├── assets_router.py    # Asset browser, download, delete
│   ├── files_router.py     # File listing, export, report
│   └── settings_router.py  # Settings, API profiles, voices
└── services/
    ├── csv_service.py          # Detection, classification, normalization
    ├── prompt_service.py       # Dynamic prompt composition
    ├── task_service.py         # Task planning + state machine
    ├── generation_service.py   # Worker pool and orchestration
    ├── google_ai_service.py    # Provider calls + error taxonomy
    ├── api_profile_service.py  # Key pool, rotation, health
    ├── asset_service.py        # Storage + verified registration
    ├── export_service.py       # ZIP packaging, reports
    ├── ffmpeg_service.py       # Merging
    ├── settings_service.py     # User defaults
    └── audit_service.py        # Activity + error logs

frontend/
├── dashboard.html · projects.html · project-detail.html
├── generation-progress.html · assets.html · export.html · settings.html
├── login.html · register.html · forgot-password.html · reset-password.html
└── static/js/app.js        # Shared API client, toasts, realtime

tests/                      # Offline verification suite
supabase_schema.sql         # Full schema, idempotent
```

---

## Tests

```bash
python tests/run_tests.py
```

Runs offline — no network, no database. 175 checks across four suites covering
CSV detection and mapping, manual overrides, prompt composition, task planning,
scene-status derivation, progress maths, storage verification, path-traversal
guards, export packaging, provider error classification, API profile rotation,
and a regression suite pinning every bug fixed after the first pass (duplicate
CSV headers, overflow cells, enumerable media URLs, stale merges, superseded
audio formats, search-filter injection, invalid ids, pause diagnostics and
client caching).

---

## API reference

| Method | Endpoint | Purpose |
|---|---|---|
| POST | `/api/upload/csv?project_id=` | Analyze (and store) a CSV |
| GET | `/api/upload/csv/{id}` · `/columns` · `/rows` | Detail, column view, paginated preview |
| POST | `/api/upload/csv/{id}/mapping` | Apply a manual column mapping |
| GET/POST | `/api/projects` | List / create |
| GET/PATCH/DELETE | `/api/projects/{id}` | Detail / rename / delete |
| POST | `/api/projects/{id}/scenes` | Import scenes (`replace` supported) |
| GET | `/api/projects/stats` | Dashboard counters |
| POST | `/api/generation/start` | Start a batch |
| GET | `/api/generation/progress/{project_id}` | Live task-based progress |
| GET | `/api/generation/tasks/{project_id}` | Task monitor |
| GET | `/api/generation/logs/{project_id}` · `/errors/{project_id}` | Activity / error logs |
| POST | `/api/generation/{job_id}/pause` · `/resume` · `/cancel` · `/retry` | Job control |
| POST | `/api/generation/scenes/{id}/retry` · `/skip` | Per-scene control |
| GET | `/api/assets` · `/summary` | Browse (search, filter, sort, paginate) |
| GET/DELETE | `/api/assets/{id}/download` · `/api/assets/{id}` | Download / delete |
| GET | `/api/files/{project_id}` | Scenes with their assets |
| POST | `/api/export` | Build a ZIP from any selection |
| GET | `/api/files/report/{project_id}` | Per-scene CSV report |
| GET/PUT | `/api/settings` | Read / update generation defaults |
| GET/POST | `/api/settings/api-profiles` | List / add profiles |
| PATCH/DELETE | `/api/settings/api-profiles/{id}` | Update / remove |
| POST | `/api/settings/api-profiles/{id}/test` · `/api/settings/test-api` | Real connection test |
| GET | `/api/settings/voices` | Available TTS voices |
| GET | `/api/health` · `/api/health/detailed` | Health checks |

---

## Troubleshooting

**"Server is missing SUPABASE_SERVICE_ROLE_KEY"**
Generation is blocked because results could not be saved. Add the `service_role`
key from Supabase → Settings → API and restart.

**"Internal server error" on Settings / Dashboard, or "column ... does not exist"**
Your database was created by an older revision of the schema. `CREATE TABLE IF
NOT EXISTS` does nothing to a table that already exists, so missing columns are
never added by it — section 7.0 of `supabase_schema.sql` reconciles them. Check
what is missing first:

```
GET /api/health/schema
```

It lists every missing table and column by name. The dashboard shows the same
thing as **Database schema: Out of date**. Re-run `supabase_schema.sql` and the
badge turns green.

**"No active Google AI API profile configured"**
Add a key in Settings and make sure the profile is Active.

**"Every configured API profile is rate limited or out of quota"**
Wait for the cooldown shown, or add another key. Cooldowns are tunable with
`QUOTA_COOLDOWN_SECONDS` / `RATE_LIMIT_COOLDOWN_SECONDS`.

**Voiceovers sound different from the voice I picked**
Cloud TTS is probably not enabled for that key, so the Gemini TTS fallback ran.
Enable Cloud Text-to-Speech in Google Cloud for named-voice support.

**Video tasks show UNSUPPORTED**
Expected unless `VIDEO_GENERATION_ENABLED=true` and your key has Veo access.

**Merge tasks show UNSUPPORTED**
FFmpeg is not on `PATH`. Images and voiceovers are unaffected.

---

## License

Private project. All rights reserved.
