# Deploying the backend to Render

Do this only after everything works locally (README steps 1-7).

## What to expect on the free plan

Render's free instance is **0.1 CPU and 512 MB RAM**, and it **sleeps after 15 minutes**
without traffic.

- **Memory: fits.** Measured on this codebase: idle ~105 MB; the heaviest flow (24 MB CSV
  upload, explore, cleaning, CSV export, 1M-cell Excel export) peaks at ~310 MB.
- **Speed: slower than your laptop.** CPU-heavy steps (parsing an upload, health of a big
  file, Excel export) can take several times longer with 0.1 CPU. Small and medium files
  stay quick; a 25 MB upload may take tens of seconds.
- **First request after sleep** takes ~30-60 s while the container starts. Nothing is lost:
  datasets live in Supabase, not in the server's memory.

If speed matters later, change `plan: free` in `render.yaml` to a paid instance, and raise
`MAX_CONCURRENT_JOBS` only if the new plan has more RAM.

## Steps

1. **Push the repo to GitHub** (`main` branch).
2. Render → **New → Blueprint** → connect GitHub → select `Insightflow-v2`.
   Render reads `render.yaml` and proposes a service called `insightflow-api` (Docker, Singapore).
3. It asks for the four secret values (`sync: false`):
   | Key | Value |
   |---|---|
   | `SUPABASE_URL` | same as your local `.env` |
   | `SUPABASE_SECRET_KEY` | same as your local `.env` |
   | `DATABASE_URL` | same as your local `.env` (Transaction pooler URI) |
   | `ALLOWED_ORIGINS` | your frontend URL(s), e.g. `https://insight-flow-final.vercel.app` (comma-separated, no trailing slash). Add `http://localhost:5173` too while developing the frontend. |
4. **Apply**. The first build takes a few minutes. Watch **Logs** for
   `starting InsightFlow API 2.0.0 (env=production)`.
5. Check it:
   ```bash
   curl https://insightflow-api-XXXX.onrender.com/health
   cd backend && python scripts/smoke_test.py you@example.com 'password' --api https://insightflow-api-XXXX.onrender.com
   ```
   Expect `29/29 checks passed`. (`/docs` is intentionally disabled in production.)

## If something goes wrong

| Symptom | Likely cause |
|---|---|
| Deploy fails at startup with `required in production` | One of the secret env vars is empty |
| `/v1/me` → 503 `auth_unavailable` | The server can't reach `SUPABASE_URL` (typo?) |
| Every `/v1` call → 401 `invalid_token` | `SUPABASE_URL` points at a different project than the one you log in to |
| Browser shows a CORS error | Frontend URL missing from `ALLOWED_ORIGINS` (exact match, no trailing slash) |
| Uploads → 503 `storage_unavailable` | Wrong `SUPABASE_SECRET_KEY`, or the `datasets` bucket wasn't created (rerun the migration) |
| Logs show `out of memory` / restarts | Lower `MAX_UPLOAD_MB` / `MAX_XLSX_UPLOAD_MB`, keep `MAX_CONCURRENT_JOBS=1` |

Every error response includes a `request_id`; search for it in Render's logs to see the full trace.
