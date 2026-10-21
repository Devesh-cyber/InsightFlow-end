// Shapes returned by the InsightFlow API (see docs/API.md in the backend repo).

export type Dtype = "integer" | "float" | "boolean" | "date" | "datetime" | "text";
export type Role = "numeric" | "categorical" | "boolean" | "temporal" | "identifier" | "free_text" | "constant" | "empty";
export type Severity = "critical" | "warning" | "info";
export type Cell = string | number | boolean | null;

export interface Operation {
  type: string;
  params: Record<string, unknown>;
}

export interface DatasetSummary {
  id: string;
  name: string;
  source_filename: string;
  source_format: "csv" | "xlsx";
  source_size_bytes: number;
  sheet_name: string | null;
  sheet_names: string[] | null;
  current_version: number;
  row_count: number;
  column_count: number;
  created_at: string;
  updated_at: string;
}

export interface ColumnInfo {
  name: string;
  dtype: Dtype;
  null_count: number;
  converted_from_text: boolean;
  missing_markers: Record<string, number>;
}

export interface Suggestion {
  id: string;
  column: string;
  kind: string;
  message: string;
  evidence: Record<string, unknown>;
  operation: Operation;
}

export interface IngestReport {
  source: { format: string; encoding?: string; delimiter?: string; sheet?: string | null; sheet_names?: string[] | null };
  rows: number;
  columns: number;
  renamed_columns: { from?: string; to?: string; [k: string]: unknown }[];
  dropped_empty_rows: number;
  dropped_empty_columns: string[];
  warnings: string[];
}

export interface DatasetDetail extends DatasetSummary {
  columns: ColumnInfo[];
  suggestions: Suggestion[];
  ingest_report: IngestReport | null;
  last_operation: Operation | { type: string };
}

export interface Me {
  user_id: string;
  email: string | null;
  usage: { datasets: number; max_datasets: number; storage_bytes: number; max_upload_mb: number; max_xlsx_upload_mb: number };
}

export interface Overview {
  version: number;
  rows: number;
  columns: number;
  cells: number;
  missing_cells: number;
  missing_pct: number;
  completeness_pct: number;
  duplicate_rows: number;
  duplicate_pct: number;
  memory_bytes: number;
  dtype_counts: Record<string, number>;
  role_counts: Record<string, number>;
  preview: { columns: { name: string; dtype: Dtype }[]; rows: Cell[][] };
}

export interface RowsPage {
  version: number;
  total_rows: number;
  offset: number;
  limit: number;
  columns: { name: string; dtype: Dtype }[];
  rows: Cell[][];
}

export interface IssueOption {
  label: string;
  operation: Operation;
  recommended: boolean;
  note: string | null;
}

export interface Issue {
  id: string;
  category: string;
  severity: Severity;
  column: string | null;
  title: string;
  message: string;
  evidence: Record<string, unknown>;
  options: IssueOption[];
  /** Which rows the issue is about (null = the whole column). */
  match?: RowMatch | null;
  /** primary = fix while cleaning; secondary = review while exploring (outliers, skew…). */
  stage?: Stage;
  fingerprint?: string;
  /** The user marked it "Looks fine, keep it". */
  reviewed?: boolean;
  /** Marked fine earlier, but the issue has changed since. */
  changed_since_review?: boolean;
}

export type Stage = "primary" | "secondary";

export interface StageSummary {
  open: number;
  reviewed: number;
  critical: number;
  warning: number;
  info: number;
  note: string;
}

export interface ReviewMark {
  issue_id: string;
  fingerprint: string;
  title?: string;
  note?: string | null;
}

export interface Health {
  version: number;
  score: number;
  grade: "excellent" | "good" | "fair" | "poor";
  deductions: { reason: string; points: number }[];
  summary: { critical: number; warning: number; info: number; by_category: Record<string, number> };
  issues: Issue[];
  notes: string[];
  stages?: Record<Stage, StageSummary>;
}

export interface ColumnProfile {
  name: string;
  dtype: Dtype;
  role: Role;
  null_count: number;
  null_pct: number;
  unique_count: number;
  unique_pct: number;
  sample_values: Cell[];
  min?: Cell;
  max?: Cell;
  mean?: number | null;
}

export interface TopValues {
  items: { value: Cell; count: number; pct: number }[];
  other_count: number;
  distinct: number;
}

export interface ColumnDetail extends ColumnProfile {
  version: number;
  rows: number;
  numeric?: {
    min: number; max: number; mean: number; median: number; std: number | null; skew: number | null; kurtosis: number | null;
    sum: number; zeros: number; negatives: number; quantiles: Record<string, number>;
    outliers: { method: string; factor: number; lower_bound: number; upper_bound: number; count: number; pct: number } | null;
  };
  histogram?: { start: number; end: number; count: number }[];
  text?: { min_length: number; mean_length: number; max_length: number; padded_with_spaces: number };
  temporal?: { min: string; max: string; span_days: number; period: string; timeline: { period: string; count: number }[]; weekday_counts: Record<string, number> };
  boolean?: { true: number; false: number };
  top_values?: TopValues;
}

export interface Pair {
  a: string;
  b: string;
  r: number;
  strength: string;
  n: number;
  small_sample: boolean;
}

export interface Correlations {
  version: number;
  method: "pearson" | "spearman";
  columns: string[];
  matrix: (number | null)[][];
  top_pairs: Pair[];
  truncated: boolean;
  note?: string;
}

export type Relationship =
  | { type: "numeric_numeric"; columns: [string, string]; n: number; pearson: number; spearman: number; r_squared: number; strength: string;
      direction: string; fit: { slope: number; intercept: number } | null; scatter: { points: [number, number][]; sampled: boolean };
      small_sample: boolean; warning: string | null; interpretation: string }
  | { type: "categorical_categorical"; columns: [string, string]; n: number; cramers_v: number; strength: string;
      chi_square: { statistic: number; dof: number; p_value: number }; contingency: { row_labels: string[]; column_labels: string[]; counts: number[][] };
      warnings: string[]; interpretation: string }
  | { type: "numeric_categorical"; columns: [string, string]; numeric_column: string; category_column: string; n: number; eta: number; eta_squared: number;
      strength: string; groups: Record<string, number | string | null>[]; interpretation: string }
  | { type: "temporal_numeric"; columns: [string, string]; n: number; period: string; spearman_vs_time: number | null;
      trend: { period: string; mean: number | null; sum: number; count: number }[]; interpretation: string };

export interface ChartOption { type: ChartType; description: string; recommended: boolean }
export type ChartType = "histogram" | "box" | "bar" | "line" | "scatter" | "heatmap";
export interface ChartOptions { x: string; y: string | null; x_kind: string | null; y_kind: string | null; charts: ChartOption[]; reason: string | null }

export interface BoxGroup { label: string; n: number; min: number; q1: number; median: number; q3: number; max: number; whisker_low: number; whisker_high: number; outlier_count: number; outliers: number[] }
export type ChartData =
  | { type: "histogram"; x: string; bins: { start: number; end: number; count: number }[] }
  | { type: "box"; x: string; y: string | null; value_column: string; group_column: string | null; groups: BoxGroup[]; truncated: boolean }
  | { type: "bar"; x: string; y: string | null; agg: string; bars: { label: string; value: number; rows: number }[]; categories_total: number }
  | { type: "line"; x: string; y: string | null; agg: string; period: string; points: { t: string; value: number | null }[] }
  | { type: "scatter"; x: string; y: string; points: [number, number][]; total_points: number; sampled: boolean }
  | { type: "heatmap"; x: string; y: string; x_labels: string[]; y_labels: string[]; counts: number[][]; truncated: boolean };

export interface Effect {
  rows_before: number; rows_after: number; rows_removed: number;
  columns_before: number; columns_after: number; columns_removed: string[]; columns_added: string[];
  columns_renamed: Record<string, string>; cells_changed: number; changed_per_column: Record<string, number>;
  dtype_changes: Record<string, { from: string; to: string }>; no_change: boolean;
}

export interface Preview {
  version: number;
  operation: Operation;
  description: string;
  effect: Effect;
  warnings: string[];
  samples: {
    changed_rows?: { columns: string[]; rows: { row: number; before: Cell[]; after: Cell[] }[] };
    removed_rows?: { columns: string[]; rows: { row: number; values: Cell[] }[] };
    new_columns?: { columns: string[]; added: string[]; rows: { row: number; values: Cell[] }[] };
  };
  columns_after: ColumnInfo[];
}

export interface VersionEntry {
  version: number;
  is_current: boolean;
  operation: Operation | { type: string };
  description: string;
  rows: number;
  columns: number;
  rows_removed: number | null;
  cells_changed: number | null;
  warnings: string[];
  created_at: string;
}

export interface Versions { current_version: number; retention: number; versions: VersionEntry[] }

export interface OperationSpec { type: string; params_schema: Record<string, unknown> }

// ------------------------------------------------------------- custom rules
export type RuleOp =
  | "eq" | "ne" | "lt" | "le" | "gt" | "ge" | "between" | "not_between"
  | "contains" | "not_contains" | "starts_with" | "ends_with" | "in" | "not_in"
  | "is_empty" | "not_empty" | "duplicate" | "invalid_number" | "invalid_date" | "invalid_email" | "differs_from";

/** Formula: alternating operand, operator, operand… */
export type FormulaOperand = { column: string } | { number: number };
export type FormulaTerm = FormulaOperand | "+" | "-" | "*" | "/";

export interface RuleCondition {
  column: string;
  op: RuleOp;
  value?: unknown;
  value2?: unknown;
  ignore_case?: boolean;
  formula?: FormulaTerm[];
  tolerance?: number;
}

export interface CheckDef {
  id: string;
  name: string;
  conditions: RuleCondition[];
  combine: "and" | "or";
}

export interface CheckResult {
  id: string;
  name: string;
  rule: string;
  status: "passing" | "failing" | "error";
  violations: number | null;
  columns?: string[];
  rows: { row: number; values: Cell[] }[];
  error: string | null;
}

export interface ChecksView {
  version: number;
  checks: (CheckDef & { result: CheckResult | null })[];
}

/** Which rows something is about: rule conditions, or every copy of a repeated row. */
export type RowMatch = { conditions: RuleCondition[]; combine: "and" | "or"; duplicate_rows?: false } | { duplicate_rows: true; conditions?: never; combine?: never };

export interface MatchingRows {
  version: number;
  total: number;
  rows_in_version: number;
  columns: string[];
  highlight: string[];
  offset: number;
  rows: { row: number; values: Cell[] }[];
}

// ------------------------------------------------------------- ML-ready (stage 3)
export type MLRole = "use" | "drop" | "target";
export type MLStep =
  | { kind: "fill_missing"; strategy: "mean" | "median" | "mode" | "constant"; value?: unknown; add_indicator?: boolean }
  | { kind: "clip"; method: "percentile" | "iqr" | "values"; lower?: number | null; upper?: number | null; factor?: number }
  | { kind: "transform"; method: "log1p" | "log" | "log10" | "sqrt" | "square" }
  | { kind: "scale"; method: "standard" | "minmax" | "robust" }
  | { kind: "encode"; method: "onehot" | "frequency" | "ordinal"; max_categories?: number; drop_first?: boolean; order?: string[] | null }
  | { kind: "bin"; method: "quantile" | "equal_width"; bins: number }
  | { kind: "date_parts"; parts: string[] }
  | { kind: "to_number" };

export interface MLColumnPlan { role: MLRole; steps: MLStep[] }
export interface MLFeature {
  name: string;
  kind?: "formula" | "condition";
  formula?: FormulaTerm[] | null;
  conditions?: RuleCondition[];
  combine?: "and" | "or";
  then_value?: number;
  else_value?: number;
  empty_stays_empty?: boolean;
}
export interface MLPlan {
  columns: Record<string, MLColumnPlan>;
  features: MLFeature[];
  split: { enabled: boolean; test_size: number; seed: number; stratify: boolean };
}
export interface MLSuggestion {
  dtype: string; role: string; suggested_role: MLRole; role_reason: string | null;
  steps: MLStep[]; reason: string | null; missing: number; unique: number;
}
export interface MLReadiness { locked: boolean; primary_open: number; secondary_open: number; version: number }
export interface MLView { version: number; rows: number; plan: MLPlan; suggestions: Record<string, MLSuggestion>; readiness: MLReadiness }
export interface MLStats { dtype: string; missing: number; mean?: number | null; std?: number | null; min?: number | null; max?: number | null; skew?: number | null; unique?: number }
export interface MLPreview {
  version: number;
  shape_before: [number, number];
  shape_after: [number, number];
  split: { how: string; train: number; test: number } | null;
  target: string | null;
  columns: { name: string; source: string; before: MLStats | null; after: MLStats }[];
  preview: { columns: string[]; rows: Cell[][] };
  warnings: string[];
  learned: Record<string, unknown[]>;
}
