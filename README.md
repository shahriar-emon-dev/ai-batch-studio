# AI Batch Studio

**AI Video Production Batch System** — Convert CSV scene data into AI-generated visuals and voiceovers using official Google APIs.

Built for large-scale content production. Upload hundreds of scenes, configure generation settings, and let the system process everything with automatic retry, resume, and progress tracking.

---

## Features

- **CSV Upload & Validation** — Drag-and-drop CSV with automatic column detection, validation, and inline editing
- **Batch Image Generation** — Generate images using Gemini-native models (gemini-2.5-flash-image, gemini-3.1-flash-image-preview)
- **Batch Video Generation** — Generate videos using Veo models (veo-3.1-fast-generate-preview)
- **Text-to-Speech** — Generate voiceovers using Google Cloud TTS (380+ voices, 75+ languages)
- **Provider Abstraction** — Modular architecture ready for additional providers
- **Multiple API Profiles** — Support for multiple Google credential profiles with manual failover
- **Job Resume** — Crash-safe: resume interrupted jobs without regenerating completed work
- **Retry Logic** — Exponential backoff (2s → 4s → 8s) with error classification
- **Quota Protection** — Detects quota/billing errors and pauses gracefully
- **Real-time Dashboard** — Live progress via Server-Sent Events (SSE)
- **Optional AI Enhancement** — Enhance visual prompts and voiceover scripts with Gemini
- **FFmpeg Merge** — Optional video + audio merging with timing control
- **Production Reports** — Export generation logs and failed scene CSVs

---

## Quick Start (Windows)

### 1. Prerequisites

- **Python 3.10+** installed
- **Google Gemini API Key** from [Google AI Studio](https://aistudio.google.com/)
- **Google Cloud Service Account** (optional, for Text-to-Speech) from [Google Cloud Console](https://console.cloud.google.com/)
- **FFmpeg** (optional, for video+audio merging) — [Download FFmpeg](https://ffmpeg.org/download.html)

### 2. Installation

```bash
cd "e:\video automation\ai_batch_studio"

python -m venv venv

venv\Scripts\activate

pip install -r requirements.txt
```

### 3. Configuration

```bash
copy .env.example .env
```

Edit `.env` and add your credentials:

```env
# Required: Gemini API key for image/video generation
GOOGLE_PROFILE_1_API_KEY=your-gemini-api-key-here

# Optional: Path to service account JSON for Google Cloud TTS
GOOGLE_PROFILE_1_TTS_CREDENTIALS=C:\path\to\service-account.json

# Optional: Second profile
GOOGLE_PROFILE_2_API_KEY=
GOOGLE_PROFILE_2_TTS_CREDENTIALS=
```

### 4. Run

```bash
python app.py
```

Open **http://127.0.0.1:8000** in your browser.

---

## Google API Setup

### Gemini API Key (Image & Video Generation)

1. Go to [Google AI Studio](https://aistudio.google.com/)
2. Click **Get API Key** → **Create API Key**
3. Copy the key into `GOOGLE_PROFILE_1_API_KEY` in your `.env` file

### Google Cloud TTS (Text-to-Speech)

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create or select a project
3. Enable the **Cloud Text-to-Speech API**
4. Go to **IAM & Admin → Service Accounts**
5. Create a service account and download the JSON key
6. Set `GOOGLE_PROFILE_1_TTS_CREDENTIALS` to the path of the JSON file

### FFmpeg (Optional)

1. Download from [ffmpeg.org](https://ffmpeg.org/download.html)
2. Add to your system PATH
3. The app auto-detects FFmpeg at startup

---

## CSV Format

### Required Columns

| Column | Description |
|--------|-------------|
| `id` | Unique scene identifier |
| `visual_prompt` | Text prompt for image/video generation |
| `voiceover_script` | Text for TTS voiceover |

### Optional Columns

| Column | Default | Description |
|--------|---------|-------------|
| `aspect_ratio` | 16:9 | 16:9, 9:16, 1:1, 4:3, 3:4 |
| `filename` | scene_{id} | Output filename (no extension) |
| `style` | — | Visual style hint |
| `negative_prompt` | — | What to avoid in generation |
| `voice` | — | Override TTS voice per scene |
| `language` | en-US | Override language per scene |
| `speaking_speed` | 1.0 | Override speech speed per scene |

### Example

```csv
id,visual_prompt,voiceover_script,aspect_ratio,filename
001,"Cinematic aerial view of ancient Rome at golden hour","Imagine standing above Rome two thousand years ago.","16:9","scene_001"
002,"Roman soldiers marching through a crowded street","Thousands of soldiers once marched through streets like these.","16:9","scene_002"
```

---

## Output Structure

```
output/
├── images/          # Generated images (.png)
├── videos/          # Generated videos (.mp4)
├── audio/           # Generated voiceovers (.wav)
├── merged/          # Merged video+audio (_final.mp4)
├── failed/          # failed_scenes.csv
└── logs/            # generation_log.csv
```

---

## Architecture

```
ai_batch_studio/
├── app.py                    # FastAPI entry point
├── config.py                 # Pydantic Settings
├── requirements.txt
├── .env.example
├── sample.csv
│
├── database/
│   ├── database.py           # Async SQLAlchemy engine
│   └── models.py             # ORM models (Job, Scene, etc.)
│
├── providers/
│   ├── base_provider.py      # Abstract provider interface
│   └── google_provider.py    # Google implementation
│
├── services/
│   ├── csv_service.py        # CSV parsing & validation
│   ├── generation_service.py # Core batch orchestrator
│   ├── speech_service.py     # Voiceover validation
│   ├── queue_service.py      # Persistent job queue
│   ├── file_service.py       # Output file management
│   ├── retry_service.py      # Exponential backoff
│   ├── merge_service.py      # FFmpeg integration
│   ├── enhancement_service.py # AI prompt/script enhancement
│   └── logging_service.py    # Activity logging & reports
│
├── routes/
│   ├── upload.py             # CSV upload endpoint
│   ├── generation.py         # Batch lifecycle + SSE
│   ├── jobs.py               # Job/scene CRUD
│   └── settings.py           # Profiles, voices, models
│
├── templates/
│   └── index.html            # SPA template
│
├── static/
│   ├── css/styles.css        # Premium dark design system
│   └── js/app.js             # SPA controller
│
├── uploads/                  # Temporary CSV uploads
├── output/                   # Generated media
└── database/
    └── ai_batch_studio.db    # SQLite database
```

---

## Generation Modes

| Mode | Visual Output | Audio Output | Merge |
|------|--------------|--------------|-------|
| **Image + Voice** | .png | .wav | — |
| **Video + Voice** | .mp4 | .wav | Optional .mp4 |
| **Image Only** | .png | — | — |

---

## API Reference

### Upload
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/upload/csv` | Upload and validate CSV |

### Generation
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/generation/start` | Start batch job |
| POST | `/api/generation/pause` | Pause running job |
| POST | `/api/generation/resume` | Resume job |
| POST | `/api/generation/cancel` | Cancel job |
| POST | `/api/generation/retry-failed` | Retry failed scenes |
| GET | `/api/generation/events` | SSE progress stream |

### Jobs
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/jobs` | List all jobs |
| GET | `/api/jobs/{id}` | Job detail |
| GET | `/api/jobs/{id}/scenes` | List scenes |
| PUT | `/api/jobs/{id}/scenes/{sid}` | Edit scene |
| DELETE | `/api/jobs/{id}/scenes/{sid}` | Delete scene |
| POST | `/api/jobs/{id}/scenes/{sid}/retry` | Retry scene |

### Settings
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/settings/profiles` | List profiles |
| POST | `/api/settings/profiles/{id}/test` | Test connection |
| GET | `/api/settings/voices` | List TTS voices |
| GET | `/api/settings/models` | List models |

---

## Troubleshooting

### "Gen AI client not initialized"
→ Add `GOOGLE_PROFILE_1_API_KEY` to `.env` and restart

### "TTS client not initialized"
→ Set `GOOGLE_PROFILE_1_TTS_CREDENTIALS` to a valid service account JSON path

### "FFmpeg not found"
→ Install FFmpeg and add to PATH. Merge features are optional.

### "Quota/billing limit reached"
→ Wait for quota reset or switch to another profile with available quota

---

## License

Private project. All rights reserved.
