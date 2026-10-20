// Friendly forms for every cleaning operation the backend supports.
// Each field maps 1:1 to a param in docs/API.md; the backend validates again.
import type { Dtype } from "./types";

export type Field =
  | { key: string; label: string; kind: "columns"; types?: Dtype[]; optional?: boolean; hint?: string }
  | { key: string; label: string; kind: "column"; types?: Dtype[] }
  | { key: string; label: string; kind: "select"; options: [string, string][]; default: string }
  | { key: string; label: string; kind: "text"; placeholder?: string; optional?: boolean; showIf?: [string, string] }
  | { key: string; label: string; kind: "number"; default: number; min?: number; max?: number; step?: number }
  | { key: string; label: string; kind: "list"; placeholder?: string; optional?: boolean; hint?: string }
  | { key: string; label: string; kind: "bool"; default: boolean }
  | { key: string; label: string; kind: "rename" };

export interface OpForm {
  type: string;
  label: string;
  help: string;
  group: "Columns" | "Missing values" | "Rows" | "Types & text" | "Numbers";
  fields: Field[];
}

const NUMERIC: Dtype[] = ["integer", "float"];

export const OP_FORMS: OpForm[] = [
  { type: "drop_columns", label: "Drop columns", group: "Columns", help: "Remove columns you don't need.",
    fields: [{ key: "columns", label: "Columns to drop", kind: "columns" }] },
  { type: "rename_columns", label: "Rename columns", group: "Columns", help: "Give columns clearer names.",
    fields: [{ key: "mapping", label: "New names", kind: "rename" }] },
  { type: "drop_empty_columns", label: "Drop empty columns", group: "Columns", help: "Remove columns with no values at all.", fields: [] },
  { type: "drop_constant_columns", label: "Drop constant columns", group: "Columns", help: "Remove columns where every value is the same.", fields: [] },

  { type: "fill_missing", label: "Fill missing values", group: "Missing values", help: "Replace empty cells. Every choice changes the data, so preview first.",
    fields: [
      { key: "columns", label: "Columns", kind: "columns" },
      { key: "strategy", label: "Fill with", kind: "select", default: "mode", options: [
        ["mean", "Mean (numbers)"], ["median", "Median (numbers, robust to outliers)"], ["mode", "Most common value"],
        ["constant", "A value I type"], ["forward", "Previous row's value"], ["backward", "Next row's value"]] },
      { key: "value", label: "Value", kind: "text", placeholder: "e.g. Unknown or 0", showIf: ["strategy", "constant"] },
    ] },
  { type: "drop_missing_rows", label: "Drop rows with missing values", group: "Missing values", help: "Remove rows that have gaps.",
    fields: [
      { key: "how", label: "Drop a row when", kind: "select", default: "any", options: [["any", "any chosen column is empty"], ["all", "all chosen columns are empty"]] },
      { key: "columns", label: "Columns to check", kind: "columns", optional: true, hint: "Leave empty to check every column." },
    ] },
  { type: "mark_missing", label: "Mark values as missing", group: "Missing values", help: "Turn placeholders like '?', '-' or 'none' into real gaps.",
    fields: [
      { key: "columns", label: "Columns", kind: "columns" },
      { key: "values", label: "Values that mean 'missing'", kind: "list", placeholder: "?, -, none" },
    ] },

  { type: "drop_duplicates", label: "Remove duplicate rows", group: "Rows", help: "Keep one copy of rows that repeat.",
    fields: [
      { key: "columns", label: "Compare on columns", kind: "columns", optional: true, hint: "Leave empty to compare whole rows." },
      { key: "keep", label: "Keep", kind: "select", default: "first", options: [["first", "the first copy"], ["last", "the last copy"]] },
    ] },
  { type: "handle_outliers", label: "Handle outliers", group: "Rows", help: "Cap or remove extreme values. Outliers are often real: check them first.",
    fields: [
      { key: "column", label: "Column", kind: "column", types: NUMERIC },
      { key: "method", label: "Method", kind: "select", default: "iqr", options: [["iqr", "IQR rule (Q1 − k·IQR, Q3 + k·IQR)"], ["zscore", "Z-score (|z| > k)"]] },
      { key: "factor", label: "k", kind: "number", default: 1.5, min: 0.5, max: 10, step: 0.5 },
      { key: "action", label: "Then", kind: "select", default: "cap", options: [["cap", "cap to the bounds (keeps rows)"], ["remove", "remove those rows"]] },
    ] },

  { type: "convert_type", label: "Change column type", group: "Types & text", help: "Convert text to numbers, dates or true/false.",
    fields: [
      { key: "column", label: "Column", kind: "column" },
      { key: "to", label: "Convert to", kind: "select", default: "float", options: [
        ["integer", "Whole number"], ["float", "Decimal number"], ["date", "Date"], ["datetime", "Date and time"], ["boolean", "True / false"], ["text", "Text"]] },
      { key: "treat_as_missing", label: "Treat these as missing", kind: "list", optional: true, placeholder: "?, -, n/a" },
      { key: "format", label: "Date format (optional)", kind: "text", optional: true, placeholder: "%d/%m/%Y", showIf: ["to", "date|datetime"] },
    ] },
  { type: "parse_formatted_number", label: "Parse formatted numbers", group: "Types & text", help: "Strip ₹, $, commas and % then convert to numbers.",
    fields: [
      { key: "column", label: "Column", kind: "column", types: ["text"] },
      { key: "is_percent", label: "Values are percentages (divide by 100)", kind: "bool", default: false },
    ] },
  { type: "trim_whitespace", label: "Trim spaces", group: "Types & text", help: "Remove leading and trailing spaces.",
    fields: [{ key: "columns", label: "Columns", kind: "columns", types: ["text"] }] },
  { type: "change_case", label: "Change letter case", group: "Types & text", help: "Make text consistent, e.g. 'mumbai' and 'Mumbai'.",
    fields: [
      { key: "columns", label: "Columns", kind: "columns", types: ["text"] },
      { key: "case", label: "Case", kind: "select", default: "title", options: [["lower", "lower case"], ["upper", "UPPER CASE"], ["title", "Title Case"]] },
    ] },
  { type: "replace_values", label: "Replace values", group: "Types & text", help: "Fix typos or merge labels.",
    fields: [
      { key: "column", label: "Column", kind: "column" },
      { key: "find", label: "Find", kind: "text" },
      { key: "replace", label: "Replace with", kind: "text", optional: true },
      { key: "match", label: "Match", kind: "select", default: "exact", options: [["exact", "whole value"], ["contains", "anywhere in the text"]] },
    ] },

  { type: "round_numbers", label: "Round numbers", group: "Numbers", help: "Round to a number of decimal places.",
    fields: [
      { key: "columns", label: "Columns", kind: "columns", types: NUMERIC },
      { key: "decimals", label: "Decimal places", kind: "number", default: 2, min: 0, max: 10, step: 1 },
    ] },
];

export function initialParams(form: OpForm): Record<string, unknown> {
  const params: Record<string, unknown> = {};
  for (const f of form.fields) {
    if (f.kind === "select" || f.kind === "number" || f.kind === "bool") params[f.key] = f.default;
    else if (f.kind === "columns" || f.kind === "list") params[f.key] = f.kind === "columns" && f.optional ? null : [];
    else if (f.kind === "rename") params[f.key] = {};
    else params[f.key] = "";
  }
  return params;
}

export function visible(field: Field, params: Record<string, unknown>): boolean {
  if (field.kind !== "text" || !field.showIf) return true;
  const [key, values] = field.showIf;
  return values.split("|").includes(String(params[key]));
}

/** Drop blank optional values so the backend's defaults apply. */
export function cleanParams(form: OpForm, params: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const f of form.fields) {
    if (!visible(f, params)) continue;
    let v = params[f.key];
    if (f.kind === "columns" && Array.isArray(v) && v.length === 0 && f.optional) v = null;
    if (f.kind === "list" && Array.isArray(v) && v.length === 0 && f.optional) continue;
    if (f.kind === "text" && v === "" && f.optional) {
      if (f.key === "replace") v = "";
      else continue;
    }
    if (f.kind === "rename") {
      v = Object.fromEntries(Object.entries(v as Record<string, string>).filter(([, n]) => n.trim() !== "").map(([o, n]) => [o, n.trim()]));
    }
    out[f.key] = v;
  }
  return out;
}

/** What's missing before preview makes sense (null = ready). */
export function missingInput(form: OpForm, params: Record<string, unknown>): string | null {
  for (const f of form.fields) {
    if (!visible(f, params)) continue;
    const v = params[f.key];
    if (f.kind === "columns" && !f.optional && (!Array.isArray(v) || v.length === 0)) return `Choose at least one column.`;
    if (f.kind === "column" && !v) return "Choose a column.";
    if (f.kind === "list" && !f.optional && (!Array.isArray(v) || v.length === 0)) return `Enter at least one value.`;
    if (f.kind === "text" && !f.optional && String(v ?? "") === "") return `Fill in "${f.label}".`;
    if (f.kind === "rename" && Object.values((v as Record<string, string>) ?? {}).every((n) => !n.trim())) return "Type at least one new name.";
  }
  return null;
}

export const OP_LABEL: Record<string, string> = {
  ...Object.fromEntries(OP_FORMS.map((f) => [f.type, f.label])),
  apply_rule: "Custom rule",
  standardize_values: "Standardize spellings",
};
