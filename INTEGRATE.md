# Features update: chaining + if/then/else

Copy each file over the same path in your repo (frontend/ → your frontend folder, backend/ and docs/ → your backend repo).
No new migration and no new packages.

## What changed
- Backend `app/engine/mlprep.py`: features run top to bottom, so a feature can use the features above it.
  New feature type `kind: "condition"`: if (conditions) then X else Y. Clear 422 if a feature uses one below it or a missing column.
- Frontend `MLReady.tsx`: each feature card has a Formula / If…then…else switch, ↑ ↓ to reorder,
  "Can use: …" hint, and a warning if it uses something that's gone or below it.
- `RuleBuilder.tsx` + `rules.ts`: fixes formula numbers showing up wrong after a reload (saved plans come back with `column: null`).
- `types.ts`: MLFeature has the new fields.

## Test
1. Backend: `pytest -q` → all pass (439 with TEST_DATABASE_URL set).
2. Frontend: `npx tsc --noEmit -p . && npm run build`.
3. In the app (cleaned dataset → ML-ready):
   - + New feature: `family_size = SibSp + Parch + 1` (or `price_per_unit = total_amount ÷ units`).
   - + New feature → pick `family_size` in its formula list (earlier features now appear).
   - + New feature → If … then … else → `family_size` is greater than `0` → then 1, else 0. Preview shows a 0/1 column, typed integer.
   - Move that feature to the top with ↑ → a warning appears; move it back → gone.
   - Reload the page → all features come back the same.
