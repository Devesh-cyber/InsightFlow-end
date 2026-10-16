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
| `/v1/datasets/{id}/relationships` | `a`, `b` | `type` = `numeric_numeric` (pearson, spearman, r_squared, fit, scatter) · `categorical_categorical` (cramers_v, chi_square{statistic,dof,p_value}, contingency) · `numeric_categorical` (eta, eta_squared, groups) · `temporal_numeric` (trend). Always `strength` + plain-English `interpretation`. Identifiers / free text → 422 with the reason |
| `/v1/datasets/{id}/correlations` | `method=pearson|spearman` | `columns, matrix (null = undefined), top_pairs` (≤30 numeric columns) |
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
`{id, category, severity (critical|warning|info), column, title, message, evidence, options[{label, operation{type, params}, recommended, note}]}`.
Send any `option.operation` straight to `/preview`, then `/apply`.

**effect**: `rows_before, rows_after, rows_removed, columns_before, columns_after, columns_removed, columns_added,
columns_renamed, cells_changed, changed_per_column, dtype_changes{col:{from,to}}, no_change`.

**samples**: `changed_rows{columns, rows[{row, before[], after[]}]}` and/or `removed_rows{columns, rows[{row, values[]}]}`
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
| `handle_outliers` | `column`, `method: iqr\|zscore`, `factor` (default 1.5 / 3), `action: cap\|remove` |

## Export

| Method | Path | Query | Returns |
|---|---|---|---|
| GET | `/v1/datasets/{id}/export` | `format=csv\|xlsx`, `version?` | File download (`Content-Disposition` filename `name_vN.ext`) |
| GET | `/v1/datasets/{id}/recipe` | | `text/x-python`: pandas script reproducing the steps (follows reverts) |

XLSX export is limited to `MAX_XLSX_EXPORT_CELLS` (default 1,000,000) → 422 `too_large_for_excel`; CSV has no limit.
