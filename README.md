# InsightFlow v2

**Understand your data before you change it.** Upload a messy CSV or Excel file, see what's in
it and what's wrong with it (with evidence), clean it step by step with a preview before every
change and one-click undo, then export the result, plus a pandas script that reproduces it.

This repository currently contains the **backend** (FastAPI + Polars + Supabase). The frontend comes next.

| Folder / file | What it is |
|---|---|
| `backend/` | The API (Python 3.12) |
| `supabase/migrations/0001_init.sql` | Database schema: run once in Supabase |
| `docs/ARCHITECTURE.md` | Design decisions and why (and what was measured) |
| `docs/API.md` | Every endpoint, for building the frontend |
| `docs/DEPLOY.md` | Putting the backend on Render |
| `render.yaml` | Render deploy configuration |

---

## Run it on your computer (step by step)

You need: **Python 3.12** (`python3.12 --version`), **git**, and the Supabase project you
already created in Phase 0. Docker is optional.

### Step 1: Get the code

**If the code is on GitHub** (recommended):

```bash
cd ~/Documents
mv insightflow-v2 insightflow-v2-old          # keep your old folder (it has your .env)
git clone https://github.com/Devesh-cyber/Insightflow-v2.git insightflow-v2
cp insightflow-v2-old/backend/.env insightflow-v2/backend/.env
cd insightflow-v2/backend
```

**If you got a zip instead:**

```bash
cd ~/Downloads
unzip -o insightflow-v2-backend.zip -d ~/Documents     # keeps your existing .env and .venv
cd ~/Documents/insightflow-v2/backend
```

### Step 2: Install and run the automated tests

```bash
python3.12 -m venv .venv            # skip if .venv already exists
source .venv/bin/activate           # do this in every new terminal
pip install -r requirements-dev.txt
pytest
```

Expected last line: **`210 passed, 11 skipped`**. The skipped ones need a throwaway Postgres
(10) or pandas (1); see "Optional checks" below.

### Step 3: Supabase (already done in Phase 0)

You already ran `supabase/migrations/0001_init.sql` and created a test user. The schema
hasn't changed since, so there's **nothing to re-run**. (Fresh project? See "Fresh Supabase project" below.)

### Step 4: Fill in `backend/.env`

Open `backend/.env` (copy from `.env.example` if it doesn't exist) and make sure these are set:

| Variable | Where to find it | Secret? |
|---|---|---|
| `SUPABASE_URL` | Supabase → Project Settings → **Data API** → Project URL | no |
| `SUPABASE_PUBLISHABLE_KEY` | Project Settings → **API Keys** → publishable key | no (used by the test scripts only) |
| `SUPABASE_SECRET_KEY` | Project Settings → **API Keys** → **secret** key (`sb_secret_…`) → reveal → copy | **YES** |
| `DATABASE_URL` | **Connect** button (top bar) → **Transaction pooler** → copy the URI, replace `[YOUR-PASSWORD]` | **YES** |

- Forgot the database password? Project Settings → Database → **Reset database password**.
- If the password contains `@ : / ? #` or spaces, either reset it to letters and digits only, or URL-encode those characters.
- Never paste the two secrets into chat, screenshots, or git (`.env` is already git-ignored).

Everything else in `.env` can stay as in `.env.example`.

### Step 5: Start the server

```bash
uvicorn app.main:app --reload
```

You should see `starting InsightFlow API 2.0.0 (env=development)` and **no** line saying
`dataset endpoints will return 503` (that line means one of the two secrets is missing).
Leave this terminal running.

### Step 6: Run the end-to-end check (new terminal)

```bash
cd ~/Documents/insightflow-v2/backend
source .venv/bin/activate
python scripts/smoke_test.py you@example.com 'your-test-password'
```

This logs in with your real Supabase user and runs every feature once: uploads, explore,
cleaning, undo, export, recipe. Then it deletes what it created. Expected:
**`29/29 checks passed`**. While it runs, you can watch files appear in Supabase →
**Storage → datasets** (they're removed at the end).

### Step 7: Try it yourself

Easiest: open **http://localhost:8000/docs**, click **Authorize**, paste a token from:

```bash
python scripts/get_token.py you@example.com 'your-test-password'
```

Uploads are easiest with curl (the raw-file body is awkward in /docs):

```bash
TOKEN=$(python scripts/get_token.py you@example.com 'your-test-password')
AUTH="Authorization: Bearer $TOKEN"

# upload
curl -s -X POST "localhost:8000/v1/datasets?filename=messy_test.csv" -H "$AUTH" \
     -H "Content-Type: application/octet-stream" --data-binary @sample_data/messy_test.csv | python -m json.tool

ID=paste-the-id-from-the-output

curl -s localhost:8000/v1/datasets/$ID/health -H "$AUTH" | python -m json.tool          # issues + options
curl -s -X POST localhost:8000/v1/datasets/$ID/preview -H "$AUTH" -H "Content-Type: application/json" \
     -d '{"type":"convert_type","params":{"column":"Age","to":"integer","treat_as_missing":["?"]}}' | python -m json.tool
curl -s -X POST localhost:8000/v1/datasets/$ID/apply -H "$AUTH" -H "Content-Type: application/json" \
     -d '{"type":"convert_type","params":{"column":"Age","to":"integer","treat_as_missing":["?"]},"expected_version":1}' | python -m json.tool
curl -s localhost:8000/v1/datasets/$ID/versions -H "$AUTH" | python -m json.tool
curl -s "localhost:8000/v1/datasets/$ID/export?format=xlsx" -H "$AUTH" -o cleaned.xlsx
curl -s localhost:8000/v1/datasets/$ID/recipe -H "$AUTH"
```

Use `--data-binary` (not `-d`) for files, or the CSV gets corrupted. Try your own messy files
too; that's the most valuable test. Every endpoint is listed in `docs/API.md`.

### Step 8 (optional): Run it the way Render will

```bash
docker build -t insightflow-api .
docker run --rm -p 8001:8000 --env-file .env --memory=512m insightflow-api
# in another terminal:
python scripts/smoke_test.py you@example.com 'your-test-password' --api http://localhost:8001
```

`--memory=512m` gives the container the same RAM as Render's free plan. One Ctrl+C stops it.

### Step 9: Deploy

Follow `docs/DEPLOY.md`.

---

## Troubleshooting

| You see | Fix |
|---|---|
| `pytest`: `ModuleNotFoundError` | `source .venv/bin/activate`, then `pip install -r requirements-dev.txt` |
| Server: `dataset endpoints will return 503` | `SUPABASE_SECRET_KEY` or `DATABASE_URL` missing in `.env` |
| Server crashes: `connection … failed` / timeout | `DATABASE_URL` wrong: use the **Transaction pooler** URI and check the password |
| `/v1/me` → 401 `invalid_token` | Token from a different project, or `SUPABASE_URL` mismatch |
| `/v1/me` → 401 `token_expired` | Tokens last 1 hour; get a new one |
| Upload → 503 `storage_unavailable` | Wrong `SUPABASE_SECRET_KEY`, or the `datasets` bucket is missing (re-run the migration) |
| `address already in use` | Something else is on port 8000: stop it, or `uvicorn app.main:app --port 8002` |

Every error includes a `request_id`; the same id is on the server's log line for that request.

## Optional checks

**The 10 real-database tests** (needs Docker; starts a throwaway Postgres, never touches Supabase):

```bash
docker run -d --name if-testdb -e POSTGRES_PASSWORD=test -p 55432:5432 postgres:16
sleep 5
docker exec -i if-testdb psql -U postgres -c "create role anon; create role authenticated; create schema auth; create table auth.users (id uuid primary key); create schema storage; create table storage.buckets (id text primary key, name text, public boolean, file_size_limit bigint);"
docker exec -i if-testdb psql -U postgres < ../supabase/migrations/0001_init.sql
TEST_DATABASE_URL=postgresql://postgres:test@localhost:55432/postgres pytest tests/test_repository_postgres.py
docker rm -f if-testdb
```

**The recipe test** runs the generated pandas script for real: `pip install pandas && pytest -k recipe_runs`.

## Fresh Supabase project

1. https://supabase.com/dashboard → New project → region **Singapore**.
2. SQL Editor → paste all of `supabase/migrations/0001_init.sql` → Run. Verify with
   `select relname, relrowsecurity from pg_class where relname in ('datasets','dataset_versions','analysis_cache');`
   (three rows, all `true`).
3. Authentication → Users → Add user → tick **Auto Confirm User**.
4. Project Settings → JWT Keys: the current key should be **ECC (P-256)** or RSA. If it's only a
   legacy secret, rotate to ECC (or put the legacy secret in `SUPABASE_JWT_SECRET`).
5. For Google login later: Authentication → Providers → Google (needed by the frontend, not the backend).
