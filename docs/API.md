# InsightFlow API reference (v2)

Base URL: `http://localhost:8000` locally, your Render URL in production.
Interactive docs (development only): `/docs`.

## Conventions

- **Auth**: every `/v1/*` endpoint needs `Authorization: Bearer <Supabase access token>`.
  The frontend gets the token from `supabase.auth.getSession()`.
- **Errors** always look like:
  ```json
  {"error": {"code": "dataset_not_found", "message": "Dataset not found.", "details": null, "request_id": "3f2c…"}}
  ```
  Show `message` to users; branch on `code`; quote `request_id` in bug reports.
- **Versions**: every change creates a new dataset version. Read endpoints take an optional
  `?version=N` (default: current). Writes take `expected_version` and return **409
  `version_conflict`** if the dataset changed meanwhile (e.g. another tab): reload and retry.
- **Column names** are passed as query parameters (`?name=…`, `?a=…&b=…`), so any characters work.
- `dtype` is one of `integer | float | boolean | date | datetime | text`.
- `role` is one of `numeric | categorical | boolean | temporal | identifier | free_text | constant | empty`.

## System

| Method | Path | Notes |
|---|---|---|
| GET/HEAD | `/health` | Liveness, no auth |
| GET | `/v1/me` | User + `usage` (datasets used/limit, storage bytes, upload limits) |
| GET | `/v1/operations` | Catalogue of cleaning operations with JSON Schemas of their params |

## Datasets

| Method | Path | Body / query | Returns |
|---|---|---|---|
| POST | `/v1/datasets?filename=x.csv[&sheet=S]` | raw file bytes, `Content-Type: application/octet-stream` | 201 DatasetDetail |
| GET | `/v1/datasets` | | `{datasets: [DatasetSummary], limit}` |
| GET | `/v1/datasets/{id}` | | DatasetDetail |
| PATCH | `/v1/datasets/{id}` | `{"name": "…"}` | DatasetDetail |
| DELETE | `/v1/datasets/{id}` | | 204 |
| GET | `/v1/datasets/{id}/sheets` | | `{sheet_names, current}` |
| POST | `/v1/datasets/{id}/sheet` | `{"sheet_name", "expected_version"}` | DatasetDetail (new version) |

Browser upload: `fetch(`${API}/v1/datasets?filename=${encodeURIComponent(file.name)}`, {method: "POST", headers: {Authorization, "Content-Type": "application/octet-stream"}, body: file})`.

**DatasetDetail** = summary fields (`id, name, source_filename, source_format, source_size_bytes,
sheet_name, sheet_names, current_version, row_count, column_count, created_at, updated_at`) plus:
- `columns`: `[{name, dtype, null_count, converted_from_text, missing_markers}]`
- `suggestions`: upload-time suggestions `[{id, column, kind, message, evidence, operation}]`
- `ingest_report`: `{source{format, encoding, delimiter, sheet, sheet_names}, rows, columns, renamed_columns, dropped_empty_rows, dropped_empty_columns, warnings}` (upload / sheet versions)
- `last_operation`: what produced the current version

Upload limits: CSV `MAX_UPLOAD_MB` (25), XLSX `MAX_XLSX_UPLOAD_MB` (10) → 413 `file_too_large`.
Quota → 403 `quota_exceeded`. Too many uploads → 429 `rate_limited` (`details.retry_after_seconds`).
Unsupported file → 415 `unsupported_file_type`. Unreadable/empty → 422.

## Explore (read-only, all accept `?version=`)

| Path | Query | Returns (main fields) |
|---|---|---|
| `/v1/datasets/{id}/overview` | | rows, columns, missing_cells/pct, completeness_pct, duplicate_rows/pct, memory_bytes, dtype_counts, role_counts, `preview{columns, rows}` (10 rows) |
| `/v1/datasets/{id}/rows` | `offset`, `limit` (≤500) | `total_rows, offset, limit, columns, rows` (rows are arrays in column order) |
| `/v1/datasets/{id}/health` | | `score` (0-100), `grade`, `deductions[{reason, points}]`, `summary`, `issues[]` (see below), `notes` |
| `/v1/datasets/{id}/columns` | | `columns[{name, dtype, role, null_count, null_pct, unique_count, unique_pct, sample_values, min?, max?, mean?}]` |
| `/v1/datasets/{id}/columns/detail` | `name` | common stats + `numeric{min,max,mean,median,std,skew,kurtosis,sum,zeros,negatives,quantiles,outliers}` + `histogram[{start,end,count}]`, or `text{lengths…}` + `top_values{items[{value,count,pct}],other_count,distinct}`, or `temporal{min,max,span_days,period,timeline,weekday_counts}`, or `boolean{true,false}` |
| `/v1/datasets/{id}/relationships` | `a`, `b` | `type` = `numeric_numeric` (pearson, spearman, r_squared, fit, scatter, n, small_sample, warning: set when fewer than 30 paired rows) · `categorical_categorical` (cramers_v, chi_square{statistic,dof,p_value}, contingency) · `numeric_categorical` (eta, eta_squared, groups) · `temporal_numeric` (trend). Always `strength` + plain-English `interpretation`. Identifiers / free text → 422 with the reason |
| `/v1/datasets/{id}/correlations` | `method=pearson|spearman` | `columns, matrix (null = undefined), top_pairs` (each with `n` and `small_sample`) (≤30 numeric columns) |
| `/v1/datasets/{id}/charts/options` | `x`, `y?` | `charts[{type, description, recommended}]` valid for those columns |
| `/v1/datasets/{id}/charts/data` | `type`, `x`, `y?`, `agg?`, `bins?` | Per type: `histogram{bins}` · `box{groups[{label,n,min,q1,median,q3,max,whisker_low,whisker_high,outliers}]}` · `bar{bars[{label,value,rows}]}` (top 20 + Other) · `line{period, points[{t,value}]}` · `scatter{points[[x,y]], sampled}` (≤5000) · `heatmap{x_labels,y_labels,counts}` |

`agg` ∈ `count | sum | mean | median | min | max`.

## Clean

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/v1/datasets/{id}/recommendations` | | `{version, issues[]}` |
| POST | `/v1/datasets/{id}/preview` | `{"type", "params"}` | `{version, operation, description, effect, warnings, samples, columns_after}`; writes nothing |
| POST | `/v1/datasets/{id}/apply` | `{"type", "params", "expected_version"}` | `{dataset: DatasetDetail, applied{description, effect, warnings}}` |
| GET | `/v1/datasets/{id}/versions` | | `{current_version, retention, versions[{version, is_current, operation, description, rows, columns, rows_removed, cells_changed, warnings, created_at}]}` |
| POST | `/v1/datasets/{id}/revert` | `{"to_version", "expected_version"}` | DatasetDetail (new version; reuses the old file) |

**Issue** (health + recommendations):
`{id, category, severity (critical|warning|info), column, title, message, evidence, options[{label, operation{type, params}, recommended, note}], match}`.
`stage`: `primary` (fix while cleaning) or `secondary` (outliers, skew, constant columns: review while exploring; never lowers the score).
`fingerprint`, `reviewed` (user marked "Looks fine, keep it"), `changed_since_review` are added by `/health`, `/recommendations` and the report.
Health also returns `stages: {primary|secondary: {open, reviewed, critical, warning, info, note}}`; the score counts open primary issues only.
`match` says which rows the issue is about: `{conditions, combine}`, `{duplicate_rows: true}`, or `null` (whole-column issue).
Send it to `POST /v1/datasets/{id}/matching-rows` (body = `match` + optional `offset`, `limit` ≤100, `version`) →
`{version, total, rows_in_version, columns[], highlight[], offset, rows[{row, values[]}]}`. Read-only; also works for a rule's or check's conditions.
Send any `option.operation` straight to `/preview`, then `/apply`.

**effect**: `rows_before, rows_after, rows_removed, columns_before, columns_after, columns_removed, columns_added,
columns_renamed, cells_changed, changed_per_column, dtype_changes{col:{from,to}}, no_change`.

**samples**: `changed_rows{columns, rows[{row, before[], after[]}]}`, `removed_rows{columns, rows[{row, values[]}]}`
and/or `new_columns{columns, added[], rows[{row, values[]}]}` (new columns next to their source)
(≤20 rows each; `row` is 1-based in the version being changed).

Apply errors: 422 `no_change`, 422 `would_empty_dataset`, 422 `invalid_parameters`, 422 `invalid_operation`, 409 `version_conflict`.

### Operations (`type` → `params`)

| type | params |
|---|---|
| `drop_columns` | `columns: [str]` |
| `rename_columns` | `mapping: {old: new}` |
| `drop_duplicates` | `columns: [str] \| null` (null = all), `keep: first\|last` |
| `drop_empty_columns` | `{}` |
| `drop_constant_columns` | `{}` |
| `drop_missing_rows` | `how: any\|all`, `columns: [str] \| null` |
| `fill_missing` | `columns: [str]`, `strategy: mean\|median\|mode\|constant\|forward\|backward`, `value` (for constant) |
| `convert_type` | `column`, `to: integer\|float\|boolean\|date\|datetime\|text`, `treat_as_missing: [str]`, `format` / `format_options`, `true_values`, `false_values`, `on_error: null\|abort` |
| `parse_formatted_number` | `column`, `is_percent` |
| `mark_missing` | `columns: [str]`, `values: [str]` |
| `trim_whitespace` | `columns: [str]` |
| `change_case` | `columns: [str]`, `case: lower\|upper\|title` |
| `replace_values` | `column`, `find`, `replace`, `match: exact\|contains` |
| `round_numbers` | `columns: [str]`, `decimals: 0-10` |
| `handle_outliers` | `column`, `method: iqr\|zscore`, `factor` (default 1.5 / 3), `action: cap\|remove`, `update_linked` (default true: when capping a column used in a detected formula like `total = units × price`, recalculate the formula's result in the capped rows; the engine records the formulas it used in `linked`) |
| `standardize_values` | `column`, `mapping: {spelling: canonical}` |
| `transform_numbers` | `column`, `method: log1p\|log\|log10\|sqrt` (default log1p), `keep_original` (default true → new column), `new_name` (default `<column>_log` / `_ln` / `_log10` / `_sqrt`). Values outside the domain become missing (with a warning). Whole column only. |
| `apply_rule` | `conditions: [Condition]` (0–10), `combine: and\|or`, `action: remove_rows\|keep_rows\|set_value\|set_missing\|clamp\|calculate`, `target` (column to change; default = first condition column), `value` (set_value), `min`/`max` (clamp), `formula` (calculate) |

**Condition**: `{column, op, value?, value2?, ignore_case?, formula?, tolerance?}`. `op`:
`eq ne lt le gt ge between not_between` (numbers/dates), `contains not_contains starts_with ends_with` (text),
`in not_in` (`value` is a list), `is_empty not_empty duplicate extra_spaces invalid_number invalid_date invalid_email` (no value),
`differs_from` (`formula`, `tolerance` default 0.01: the row's value ≠ the formula's result).
Missing cells never match a comparison. **Formula**: alternating `[operand, "+"|"-"|"*"|"/", operand, …]`,
operand = `{"column": name}` or `{"number": 3}`; normal precedence; x / 0 → missing.
Example "remove rows where age < 0 or age > 120":
`{"type":"apply_rule","params":{"conditions":[{"column":"age","op":"lt","value":0},{"column":"age","op":"gt","value":120}],"combine":"or","action":"remove_rows"}}`

### Saved checks

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/v1/datasets/{id}/checks` | `?version=` | `{version, checks[{id, name, conditions, combine, result}]}` |
| PUT | `/v1/datasets/{id}/checks` | `{"checks": [{id, name, conditions, combine}]}` (replaces the list, ≤25) | same as GET (current version) |

A check's conditions describe a **bad** row. `result = {status: passing|failing|error, violations, rows[{row, values}], columns, rule, error}`.
Failing checks appear in `/health`, `/recommendations` and the report as `check_failed` issues (−5 each, max −30);
a check whose column no longer exists shows as `check_error` (info). Needs migration `0002_checks.sql`.

### Looks fine, keep it

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/v1/datasets/{id}/reviewed` | | `{reviewed[{issue_id, fingerprint, title, note}]}` |
| PUT | `/v1/datasets/{id}/reviewed` | `{"reviewed": [...]}` (replaces the list, ≤300) | same |

A mark only applies while the issue's `fingerprint` matches; if the issue changes (other rows or counts) it reopens with
`changed_since_review: true`. Needs migration `0003_reviewed.sql`.

**Issue categories**: `missing_values duplicates empty_column constant_column convert_to_number convert_to_date outliers`
plus validity checks `duplicate_id invalid_values broken_relationship inconsistent_spelling`, `check_failed check_error`, and `skewed` (secondary).

## ML-ready dataset (stage 3)

The user's own rules per column, applied to the **current version** (the cleaned data is not changed).
**Locked until the data is clean:** `PUT plan`, `preview` and `export` return **409 `clean_first`** while primary cleaning or
explore & review has open items (issues marked "looks fine" count as done); `GET /ml` always works and returns
`readiness: {locked, primary_open, secondary_open, version}`. A cleaning step that opens new issues locks it again (the plan is kept).

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/v1/datasets/{id}/ml` | | `{version, rows, plan, suggestions{column: {dtype, role, suggested_role, role_reason, steps[], reason, missing, unique}}}` |
| PUT | `/v1/datasets/{id}/ml/plan` | plan | `{plan}` |
| POST | `/v1/datasets/{id}/ml/preview` | plan (unsaved is fine) | `{version, shape_before, shape_after, split{how, train, test}?, target, columns[{name, source, before, after}], preview{columns, rows}, warnings, learned}` |
| GET | `/v1/datasets/{id}/ml/export` | | saved plan → `<name>_vN_ml_ready.csv`, or `.zip` (`train.csv`, `test.csv`, `plan.json`, `README.txt`) when the split is on |

**Plan**: `{columns: {name: {role: use|drop|target, steps: [...]}}, features: [{name, formula}], split: {enabled, test_size 0.05-0.5, seed, stratify}}`.
At most one target (it becomes the last column; rows with an empty target are left out). Features are computed first (formula of numeric columns).
**Steps** (in order, per column): `fill_missing{strategy mean|median|mode|constant, value, add_indicator}`,
`clip{method percentile|iqr|values, lower, upper, factor}`, `transform{method log1p|log|log10|sqrt|square}`,
`scale{method standard|minmax|robust}`, `encode{method onehot|frequency|ordinal, max_categories, drop_first, order}`,
`bin{method quantile|equal_width, bins}`, `date_parts{parts[year|month|day|weekday|dayofyear|is_weekend|hour]}`, `to_number{}`.
One-hot and date parts split the column, so nothing can follow them. Steps that learn (fill, clip, scale, encode, bin) learn from
the train rows when the split is on; the values are returned in `learned` / `plan.json`. Formulas match scikit-learn defaults
(population std, linear percentiles, A→Z ordinal). Needs migration `0004_ml_plan.sql`.

## Export

| Method | Path | Query | Returns |
|---|---|---|---|
| GET | `/v1/datasets/{id}/export` | `format=csv\|xlsx`, `version?` | File download (`Content-Disposition` filename `name_vN.ext`) |
| GET | `/v1/datasets/{id}/recipe` | | `text/x-python`: pandas script reproducing the steps (follows reverts) |

XLSX export is limited to `MAX_XLSX_EXPORT_CELLS` (default 1,000,000) → 422 `too_large_for_excel`; CSV has no limit.

## Report

| Method | Path | Query | Returns |
|---|---|---|---|
| GET | `/v1/datasets/{id}/report` | `format=html\|docx\|json` (default html), `version?`, `title?` (≤120), `author?` (≤80), `tz?` (IANA zone, e.g. `Asia/Kolkata`) | `html`: print-ready A4 page in the Sage theme (download; the user saves it as PDF with Ctrl/⌘+P) · `docx`: editable Word file, same content · `json`: the report data, for an in-app preview |

Sections, in order: cover (title, prepared by, dataset, source, version, date) · 1 Summary (KPI tiles + auto-written key findings) · 2 The data (data dictionary) · 3 Data quality (before vs after table, score deductions, open issues with suggested fixes) · 4 Cleaning log (every version, cells changed, rows removed, warnings) · 5 Column profiles (stats + chart per column, up to 25 columns) · 6 Relationships (top correlations with row counts; under 30 rows flagged) · 7 Reproduce it (pandas recipe) · 8 Methods and definitions.

"Before" is the earliest kept version (v1 unless old versions were pruned, which the report says). The report reuses the Explore caches, so generating it after browsing the app is cheap. The HTML has no JavaScript and is served with a strict `Content-Security-Policy`; every value from the file is escaped. Template: `backend/app/report/templates/report.html.j2` (colours are CSS variables at the top).
