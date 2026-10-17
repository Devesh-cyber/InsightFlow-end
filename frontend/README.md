# InsightFlow frontend (v2)

React + TypeScript + Vite, in the **Sage** theme with light and dark mode. It talks only to the
InsightFlow v2 backend; Supabase is used just for sign-in (email + Google).

## Run it on your computer

You need **Node.js 20 or newer** (`node --version`). Start the backend first (it should be running at
`http://localhost:8000`).

1. Put this `frontend/` folder next to `backend/` in your project.
2. Open a terminal in `frontend/` and install packages:
   ```bash
   npm install
   ```
3. Create your settings file:
   ```bash
   cp .env.example .env
   ```
   Open `.env` and fill in three values (all are public, safe for a browser):
   - `VITE_API_URL` = `http://localhost:8000`
   - `VITE_SUPABASE_URL` = Supabase dashboard → Project Settings → API → Project URL
   - `VITE_SUPABASE_PUBLISHABLE_KEY` = same page → the **publishable** key (`sb_publishable_…`)

   Never put the secret key or `DATABASE_URL` here.
4. Check the backend allows this address: in `backend/.env`, `ALLOWED_ORIGINS` must include
   `http://localhost:5173` (it does by default). Restart the backend after any change.
5. In Supabase → Authentication → URL Configuration, add `http://localhost:5173` to **Redirect URLs**
   (needed for Google sign-in and password-reset links).
6. Start the app:
   ```bash
   npm run dev
   ```
   Open http://localhost:5173 and sign in.

Other commands: `npm run build` (type-check + production build into `dist/`), `npm run preview`
(serve that build at http://localhost:4173), `npm run typecheck`.

## Deploy on Vercel

1. Push the repo, then in Vercel: **Add New → Project**, pick the repo, set **Root Directory** to `frontend`.
   Framework preset: Vite (build `npm run build`, output `dist`). `vercel.json` already handles page
   refreshes on any route and sets safe headers.
2. Add the three `VITE_…` variables under **Settings → Environment Variables**. `VITE_API_URL` is your
   Render backend URL (no trailing slash).
3. On Render, set the backend's `ALLOWED_ORIGINS` to your Vercel address, e.g.
   `https://insightflow.vercel.app` (comma-separate several; keep `http://localhost:5173` if you still develop locally).
4. In Supabase → Authentication → URL Configuration, set **Site URL** to the Vercel address and add it to
   **Redirect URLs**.

The free Render server sleeps after 15 minutes; the first request then takes ~30 s. The app retries
automatically and shows a clear message if the server can't be reached.

## What's where

```
src/
  main.tsx, App.tsx        entry, routes (login, datasets, the 8 dataset pages)
  styles/tokens.css        every colour, light + dark (Sage). Change the theme here.
  styles/base.css          layout and component styles
  lib/api.ts               the only code that calls the backend (auth header, errors, downloads, upload progress)
  lib/types.ts             response shapes (mirror backend docs/API.md)
  lib/operations.ts        forms for the 15 cleaning operations
  lib/format.ts            number / percent / date formatting
  hooks/data.ts            data fetching (cached per dataset version)
  components/charts/       SVG charts: bars, histogram, box, scatter, line, heatmap
  pages/                   Login, Datasets, Overview, Health, Columns, Relationships, Charts, Clean, History, Export
```

How data stays fresh: every query is keyed by the dataset's current version. Applying a cleaning step,
reverting or switching sheets creates a new version, so every page refetches; going back to a version
you've already seen is instant.

Theme: the Light / Auto / Dark switch is at the bottom of the sidebar. Auto follows the computer's setting.
Chart colours were checked for contrast and colour-blind separation in both themes (negative
correlations use plum rather than red, so they stay distinct from green for red-green colour blindness).
