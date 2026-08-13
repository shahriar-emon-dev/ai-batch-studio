# Deploying AI ContentStudio

Frontend on **Vercel**, API + generation worker on **Render**, database and auth on
**Supabase**.

## Why the split

Vercel functions are killed once the HTTP response is sent (10–60 s). Batch
generation runs for minutes-to-hours in background workers that hold in-memory
job state, so it cannot run there. Render web services keep the process alive
between requests, which is what the queue needs.

```
Browser ──> Vercel (static pages)
   │
   └──JWT──> Render (FastAPI + worker) ──> Supabase (data, auth)
                     │
                     └──> Google AI ──> /tmp/output (ephemeral media)
```

> **Generated media is not persisted.** Render's filesystem is wiped on every
> deploy, restart and idle-sleep. Export what you need in the same session. On
> startup the API deletes asset rows whose files are gone and requeues their
> tasks, so the UI never shows a ✓ for media that no longer exists.

---

## 1. Supabase

1. Run [`supabase_schema.sql`](supabase_schema.sql) in the SQL editor (idempotent).
2. **Authentication → URL Configuration**:
   - *Site URL*: `https://<your-project>.vercel.app`
   - *Redirect URLs*: add `https://<your-project>.vercel.app/**`
   Without this, email confirmation and password reset links point at localhost.
3. Collect from **Settings → API**: Project URL, publishable/anon key,
   service_role key, and JWT secret.

---

## 2. Render (API + worker)

**New → Blueprint** and select this repository; `render.yaml` is detected
automatically. Then set the secret variables (they are marked `sync: false`):

| Variable | Value |
|---|---|
| `SUPABASE_URL` | your project URL |
| `SUPABASE_KEY` | publishable / anon key |
| `SUPABASE_SERVICE_ROLE_KEY` | **required** — the worker cannot save results without it |
| `SUPABASE_JWT_SECRET` | optional; verifies sessions locally instead of a round trip |
| `ENCRYPTION_KEY` | **must be the same value you already use**, or previously saved Google API keys cannot be decrypted |
| `ALLOWED_ORIGINS` | `https://<your-project>.vercel.app` (comma-separate extras) |

After the first deploy, confirm:

```
https://<service>.onrender.com/api/health          -> {"status":"ok"}
https://<service>.onrender.com/api/health/schema   -> {"status":"ok"}
https://<service>.onrender.com/api/health/detailed -> worker.can_persist_results: true
```

If `can_persist_results` is `false`, `SUPABASE_SERVICE_ROLE_KEY` is missing or
malformed — generation will refuse to start until it is fixed.

### Free plan caveats
- Sleeps after ~15 minutes idle; the next request takes ~50 s to wake. The
  frontend allows for this with a 60 s timeout.
- **A sleep mid-batch kills the run.** Interrupted tasks are reopened on the
  next start, and completed assets are lost with the disk. For real batches use
  the **Starter** plan so the service never sleeps.

---

## 3. Vercel (frontend)

**Currently deployed:** https://ai-batch-studio-mug7fxeg0-aist2.vercel.app
(project `ai-batch-studio-app`, team `aist2`).

The frontend is a pure static site, so `frontend/` is deployed as its own root.
Vercel 56+ auto-detects the FastAPI backend at the repository root and refuses
a top-level static config alongside it — deploying the subdirectory avoids the
conflict entirely.

### CLI (what was used)

```bash
# 1. bake the browser config into frontend/env.js
SUPABASE_URL="https://<project>.supabase.co" \
SUPABASE_KEY="<publishable key>" \
API_BASE_URL="https://<service>.onrender.com" \
node scripts/generate-frontend-env.js

# 2. deploy the static site
cd frontend && vercel deploy --prod
```

### Git-based deploys

Import the repo and set **Root Directory = `frontend`**. Because `env.js` is
generated, either commit it or add a build command that regenerates it with
"Include files outside root directory" enabled.

| Variable | Value |
|---|---|
| `SUPABASE_URL` | your project URL |
| `SUPABASE_KEY` | publishable / anon key — **never** the service_role key |
| `API_BASE_URL` | `https://<service>.onrender.com` (optional; see below) |

`SUPABASE_URL` and `SUPABASE_KEY` are required — the build fails without them,
and the generator refuses to run if `SUPABASE_KEY` looks like a service-role key.

`API_BASE_URL` is optional so the frontend can go live before the API exists.
**Without it only login and registration work** (they talk to Supabase
directly); every other screen reports "Backend API is not configured for this
deployment".

### Deployment protection

New projects may have Vercel Authentication (SSO) enabled, which 302-redirects
every request to `vercel.com/sso-api` so only team members can view the site:

```bash
vercel project protection disable <project-name> --sso   # make public
vercel project protection enable  <project-name> --sso   # re-gate it
```

After deploying, set `ALLOWED_ORIGINS` on Render to the real Vercel URL and
redeploy the API.

---

## 4. Verify the deployment

1. Open the Vercel URL → redirected to `/login.html`.
2. Register, confirm the email, sign in.
3. **Settings** → add a Google AI profile → **Test key** → expect
   `Connection successful — N models available`.
4. **Projects** → create one → upload a CSV → check the detected mapping →
   **Apply Mapping & Import Scenes**.
5. **Batch Generation** → **Start** → tasks appear in the Task Monitor and
   progress advances.
6. **Asset Browser** → previews render and audio plays.
7. **Selective Export** → download the ZIP *before* the service restarts.

---

## Troubleshooting

**"Cannot reach the API at …"** — `ALLOWED_ORIGINS` on Render does not include
the Vercel origin, or `API_BASE_URL` on Vercel is wrong. Both must be the exact
scheme + host with no trailing slash.

**Requests time out on first use** — the free Render service is waking. Retry.

**"Server is missing SUPABASE_SERVICE_ROLE_KEY"** — set it on Render and redeploy.

**"Database schema is out of date"** — re-run `supabase_schema.sql`; check
`/api/health/schema` for the exact missing columns.

**Saved API profiles stop decrypting** — `ENCRYPTION_KEY` differs from the one
used when they were saved. Restore the original value or re-enter the keys.

**Broken thumbnails / assets vanished** — expected on an ephemeral disk after a
restart. The rows are cleared automatically and the tasks requeued; regenerate
or use a host with a persistent disk.

**Images fail with `limit: 0` quota errors** — the Google project has no image
quota on the free tier. Enable billing on that project.

**Video / merge tasks show UNSUPPORTED** — Veo is off by default
(`VIDEO_GENERATION_ENABLED`), and FFmpeg is not installed on Render's Python
runtime (`MERGE_ENABLED=false`). Both are deliberate, not failures.

---

## Deploying updates

Push to `main` — both platforms redeploy automatically. Re-run
`supabase_schema.sql` whenever the schema section changes.
