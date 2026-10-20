# InsightFlow update: custom rules, saved checks, honest cleaning

## What's in this zip

Only changed and new files, at the same paths as in your repos.

```
backend/      -> copy into your backend repo's backend/ folder
supabase/     -> migrations/0002_checks.sql (new)
docs/API.md   -> updated reference
frontend/src/ -> copy into your frontend project's src/ folder
```

Copy everything over your existing files (it overwrites the changed files and adds the new ones). Nothing gets deleted.

## What changed (simple version)

**Update 3: clean in stages, so users don't over-clean**
- **3 stages, shown in order:** ① Primary cleaning → ② Explore & review → ③ ML-ready (coming soon).
- **Primary:** types, spellings, spaces, duplicates, invalid or impossible values, broken totals, gaps, your checks. This is what you see first, and the only thing the score counts.
- **Explore & review:** outliers, skew, constant columns. Hidden behind "finish primary cleaning first" until primary is done (you can still open them). They also appear on the **Columns** page next to that column's chart. They're labelled "Note", and fixes only show after "I checked: these look wrong".
- **"Looks fine, keep it" / "These are real, keep them"** on every issue. It closes the issue **without changing the data**. Kept items move to a "Reviewed and kept" list with Undo, and they reopen automatically if the issue changes later.
- When primary is done: **"Primary cleaning done ✓"**, which points you to exploring.
- **ML-ready** tab: explains what's coming (target + model type → only the prep that model needs, exported as a scikit-learn pipeline) and links to Export for now.
- **Overview** shows only open primary issues.
- **Calculated columns** (e.g. `total_amount = units × unit_price`): their outliers explain where they come from and offer no Cap. Cap on an input column warns that it breaks the formula. This stops the cap ↔ recalculate loop.
- **Skew** is measured without far-out values, so one wrong entry isn't called "skew".
- **Report:** split into primary open issues, secondary notes and "Reviewed and kept". The findings say which stage you're at and list any columns you removed.
- **Migration `0003_reviewed.sql`** (run after 0002).


**Update 2: smoother cleaning flow**
- **Apply no longer jumps to History.** You stay on the page you were on and get a toast with an **Undo** button. A fix previewed from Health or Overview brings you back there. Only the newest Undo is clickable, and if newer changes exist it tells you to use History.
- **Show rows** on every issue opens the actual rows from your dataset that the issue is about, with the problem column highlighted and shown first. Repeated rows appear next to each other. It shows up to 100 rows, with "Show more".
- **Show matching rows** in the custom rule builder lets you see which rows your conditions hit before you pick an action.
- **Show failing rows** on saved checks.
- New rule operator: *has spaces at the start or end*.
- Backend: new read-only endpoint `POST /v1/datasets/{id}/matching-rows`, and every issue now has a `match` field.


**New: custom rule panel** (Clean → Build your own → *Custom rule*)
- Pick a column, an operator (<, >, between, is one of, contains, is empty, is repeated, isn't a valid email/date/number, doesn't equal a formula) and a value.
- Add more conditions and choose AND or OR.
- Then pick an action:
  - remove those rows
  - keep only those rows
  - make a column empty
  - set a value
  - keep within limits
  - recalculate a column with a formula (e.g. `total = units × price`)
- Every rule is previewed first and becomes a new version, like other steps.
- Example: "age < 0 OR age > 120 → remove rows" now works.

**New: saved checks**
- In the same panel: name the conditions and choose **Save check**.
- A check never changes data. It's re-run on every version. Failing checks:
  - lower the Health score (−5 each, max −30)
  - show up in "Issues to review" with fixes you can preview
  - appear in the report
- The *Saved checks* list shows passing / failing / can't run, with example rows.

**Automatic detection (works on any dataset, not tuned to one file)**
- Repeated IDs: a column that's almost unique but has a few repeats.
- Invalid emails in a mostly-email column.
- Rare negatives in a column that's otherwise never negative; rare zeros in a count column.
- Spelling variants that differ only by case or spaces (`Mumbai` / `mumbai `). The one-click fix uses the most common spelling.
- Broken arithmetic: when `C = A × B` (or `A + B`) holds in ≥90% of rows, the rows that break it are flagged.
- Mostly-valid ISO dates with a few bad ones get "convert to date".

**Safer cleaning**
- Estimates (mean, median, mode, Unknown, cap) are **never marked "recommended"** any more. Only fixes that can't distort data are (exact duplicates, trimming spaces, converting types, standardizing spellings).
- No "most common value" fill when every value is unique (dates, emails, IDs). The engine refuses it too.
- No "Unknown" fill for email, date or ID columns.
- Numbers stored as text: you're told to convert first instead of being offered a category fill.
- Preview warns when a step breaks a total. Example: capping `unit_price` gives "makes total_amount ≠ units × unit_price in 4 more rows".
- Capping explains that capped values are estimates. If the IQR lower limit is below 0, it suggests a custom rule instead.

**Honest report**
- The findings say how many changed cells are estimates, and the cleaning log tags those steps "estimate".
- Saved checks that still fail are listed. "Before" is judged by the same checks, so the comparison is fair.
- Cover page fixed (no blank second page when printing to PDF).

**Fixes found while testing**
- A type-only change (e.g. converting a text column to numbers) was wrongly treated as "no change".
- Recalculating `units × price` marked correct totals as changed because of float noise (5508.69 vs 5508.690000000001).
- Overview cards no longer show a default "Preview: cap…" when nothing is recommended. They show "Review options →" instead.
- The pandas recipe matches the app on every operation, including rules.

## Steps to integrate

1. **Run the database migrations (once, in order).** Supabase Dashboard → SQL Editor → New query → paste `supabase/migrations/0002_checks.sql` → Run, then do the same with `0003_reviewed.sql`. Both are safe to run twice. Without them, saved checks and "Looks fine" fail.
2. **Backend.** Copy `backend/` over your backend, then:
   ```bash
   cd backend
   pip install -r requirements-dev.txt     # adds pandas (tests only; production requirements are unchanged)
   pytest -q                               # expect: 377 passed, 14 skipped
   ```
   No new environment variables. Old cached health results are recomputed automatically (the engine version is part of the cache key).
3. **Frontend.** Copy `frontend/src/` over your `src/`, then:
   ```bash
   npm run build      # must finish with no TypeScript errors
   ```
   No new packages or env vars.
4. Commit and push both repos yourself. Render redeploys the backend and Vercel the frontend.

## How to test (about 10 minutes, use the anomaly CSV)

Upload the original anomaly CSV, then:

| # | Do this | You should see |
|---|---|---|
| 1 | Open **Health** | Score well below 100 (around 70, "fair"). Issues include repeated `order_id`, invalid email, negative `unit_price`, zero `units`, `total_amount` ≠ `units × unit_price` (rows 32, 38, 66, 71, 76), city spelling variants |
| 2 | **Clean** → look at the issues | No fill/cap option says "recommended". `order_date` / `customer_email` offer no "most common value" or "Unknown" |
| 3 | Custom rule: `unit_price` *is less than* `0` → *Remove those rows* → Preview | 1 row to remove (row 38). Apply → version 2 |
| 4 | Custom rule: `units` *is* `0`, add condition `units` *is greater than* `100`, choose **any (OR)** → *Make a column empty* → `units` → Preview | 1 changed row (0 → missing). Go back (don't need to apply) |
| 5 | Condition `total_amount` *doesn't equal formula* `units × unit_price`, name it "Total = units × price" → **Save check** | Saved checks shows **failing · N rows** with example rows. A red "Your check … fails" issue appears at the top of Issues to review |
| 6 | Open **Health** | Deduction "Your checks failing" is listed and the score dropped |
| 7 | Custom rule: *Recalculate a column with a formula*, remove the condition, column `total_amount` = `units × unit_price` → Preview → Apply | Only the wrong totals change (correct ones aren't listed). The check now says **passing** |
| 8 | Quick step → Handle outliers → `unit_price`, cap → Preview | Warning that capping makes totals stop adding up |
| 9 | **Export & report** → open the HTML report → print to PDF | Page 2 is the Summary (not blank). Findings mention estimates (if you capped or filled) and failing checks |
| 10 | Download the **recipe**, run it on the original CSV with pandas | Same rows and values as the app's CSV export |
| 11 | Try another CSV of your own | Clean data shouldn't get validity warnings. Real problems should be found |
| 12 | Delete a saved check (✕) | It disappears and Health goes back up |
| 13 | On any issue, click **Show rows** | A table of the real rows. The problem column is highlighted and first (e.g. the `not-an-email` row) |
| 14 | Apply any fix | You stay on the same page. Toast says "Applied … Now on version N" with **Undo** |
| 15 | Click **Undo** in that toast | A new version matching the previous one (History shows "Reverted to version …") |
| 16 | From **Health**, preview a fix and apply | You land back on Health, not History |
| 17 | In the rule builder, set `units` *is at least* `7` → **Show matching rows** | Only those rows, with `units` highlighted |
| 18 | Open **Overview** on a fresh upload | Only primary issues, no outliers |
| 19 | **Clean** | The stage strip opens on ① Primary cleaning. No outliers or skew in the list |
| 20 | Click ② **Explore & review** while primary issues remain | "Finish primary cleaning first (N left)" with "show anyway" |
| 21 | Click **Looks fine, keep it** on any issue | Toast "Kept as is… data wasn't changed". It moves to "Reviewed and kept (1)", the score goes up, and Undo brings it back |
| 22 | Fix or keep all primary issues | "Primary cleaning done ✓" banner pointing to Explore |
| 23 | **Columns** → `units` | "To review in 'units'" shows its outlier note under the chart |
| 24 | ② tab → the `total_amount` note | It says it's calculated from units × unit_price and offers no Cap |
| 25 | ③ **ML-ready** | Coming-soon card with a checklist and a link to Export |
| 26 | Report | Primary issues, secondary notes and "Reviewed and kept" are separate sections |

Edge cases worth a click:
- Save a check on a column, then drop that column. The check shows "can't run", nothing crashes.
- A text column with numbers in a `<` rule. You get a clear message to convert the type first.
- Save 25 checks. The 26th is refused with a message.

## API (for reference)

- `GET  /v1/datasets/{id}/checks?version=`: lists the saved checks and their results.
- `PUT  /v1/datasets/{id}/checks` with body `{checks:[{id,name,conditions,combine}]}`: replaces the whole list.
- New operations: `apply_rule`, `standardize_values`.
- Full details are in `docs/API.md`.
