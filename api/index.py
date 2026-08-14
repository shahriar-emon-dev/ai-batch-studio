"""Vercel serverless entrypoint for the FastAPI app.

Vercel routes every `/api/*` request here. The app object is imported
unchanged so local (`python -m backend.app`) and deployed behaviour stay
identical.

Note: Vercel functions are terminated once a response is sent, so the
background generation worker cannot run here. Starting a batch is refused with
a clear message rather than appearing to work — see backend/app.py.
"""

import os
import sys

# The repo root must be importable so `backend.*` resolves inside the bundle.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# The only writable location in a Vercel function.
os.environ.setdefault("OUTPUT_DIR", "/tmp/output")
os.environ.setdefault("UPLOADS_DIR", "/tmp/uploads")

from backend.app import app  # noqa: E402

# Vercel's Python runtime looks for a module-level ASGI callable named `app`.
__all__ = ["app"]
