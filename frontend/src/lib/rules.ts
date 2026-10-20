// Custom rules ("if column operator value → action") and saved checks.
// Mirrors backend app/engine/rules.py; the backend validates everything again.
import type { ColumnInfo, Dtype, FormulaTerm, Operation, RuleCondition, RuleOp } from "./types";

export const OP_TEXT: Record<RuleOp, string> = {
  eq: "is", ne: "is not", lt: "is less than", le: "is at most", gt: "is greater than", ge: "is at least",
  between: "is between", not_between: "is not between",
  contains: "contains", not_contains: "does not contain", starts_with: "starts with", ends_with: "ends with",
  in: "is one of", not_in: "is not one of",
  is_empty: "is empty", not_empty: "is not empty", duplicate: "is repeated",
  invalid_number: "is not a valid number", invalid_date: "is not a valid date", invalid_email: "is not a valid email",
  differs_from: "doesn't equal formula",
};

const COMMON: RuleOp[] = ["is_empty", "not_empty", "duplicate"];

export function opsFor(dtype: Dtype | undefined): RuleOp[] {
  switch (dtype) {
    case "integer":
    case "float":
      return ["lt", "le", "gt", "ge", "eq", "ne", "between", "not_between", "in", "not_in", ...COMMON, "differs_from"];
    case "date":
    case "datetime":
      return ["lt", "le", "gt", "ge", "eq", "ne", "between", "not_between", ...COMMON];
    case "boolean":
      return ["eq", "ne", "is_empty", "not_empty"];
    default:
      return ["eq", "ne", "contains", "not_contains", "starts_with", "ends_with", "in", "not_in", ...COMMON,
        "invalid_number", "invalid_date", "invalid_email"];
  }
}

export type ValueShape = "none" | "one" | "two" | "list" | "formula";

export function valueShape(op: RuleOp): ValueShape {
  if (op === "between" || op === "not_between") return "two";
  if (op === "in" || op === "not_in") return "list";
  if (op === "differs_from") return "formula";
  if (["is_empty", "not_empty", "duplicate", "invalid_number", "invalid_date", "invalid_email"].includes(op)) return "none";
  return "one";
}

export const isTextOp = (op: RuleOp) => ["eq", "ne", "contains", "not_contains", "starts_with", "ends_with", "in", "not_in"].includes(op);

/** Editable condition: values are kept as typed text until sent. */
export interface DraftCondition {
  column: string;
  op: RuleOp;
  value: string;
  value2: string;
  ignoreCase: boolean;
  formula: FormulaTerm[];
}

export const emptyCondition = (columns: ColumnInfo[]): DraftCondition => {
  const first = columns[0];
  return { column: first?.name ?? "", op: opsFor(first?.dtype)[0], value: "", value2: "", ignoreCase: false, formula: [] };
};

const isNumeric = (d?: Dtype) => d === "integer" || d === "float";

function literal(text: string, dtype: Dtype | undefined): unknown {
  const t = text.trim();
  if (isNumeric(dtype)) return Number(t);
  if (dtype === "boolean") return t === "true";
  return text; // text keeps its spaces; dates go as YYYY-MM-DD
}

export function formulaProblem(terms: FormulaTerm[]): string | null {
  if (terms.length === 0 || terms.length % 2 === 0) return "Finish the formula (value, operator, value…).";
  for (let i = 0; i < terms.length; i += 2) {
    const t = terms[i] as { column?: string; number?: number };
    if (typeof t !== "object" || (("column" in t) ? !t.column : !Number.isFinite(t.number))) return "Every part of the formula needs a column or a number.";
  }
  return null;
}

export function conditionProblem(c: DraftCondition, columns: ColumnInfo[]): string | null {
  const col = columns.find((x) => x.name === c.column);
  if (!col) return "Choose a column.";
  const shape = valueShape(c.op);
  const numberOk = (s: string) => s.trim() !== "" && Number.isFinite(Number(s));
  const dateOk = (s: string) => /^\d{4}-\d{2}-\d{2}/.test(s.trim());
  const check = (s: string) => {
    if (isNumeric(col.dtype) && !numberOk(s)) return `'${c.column}' is a number column: type a number.`;
    if ((col.dtype === "date" || col.dtype === "datetime") && !dateOk(s)) return "Pick a date.";
    if (col.dtype === "text" && s === "" && shape !== "one") return "Type a value.";
    return null;
  };
  if (shape === "one") return check(c.value);
  if (shape === "two") {
    const p = check(c.value) ?? check(c.value2);
    if (p) return p;
    if (isNumeric(col.dtype) && Number(c.value) > Number(c.value2)) return "The first number should be the smaller one.";
    return null;
  }
  if (shape === "list") {
    const items = c.value.split(",").map((s) => s.trim()).filter(Boolean);
    if (!items.length) return "Type at least one value (comma separated).";
    if (isNumeric(col.dtype) && !items.every(numberOk)) return "Every value in the list must be a number.";
    return null;
  }
  if (shape === "formula") return formulaProblem(c.formula);
  return null;
}

export function toCondition(c: DraftCondition, columns: ColumnInfo[]): RuleCondition {
  const dtype = columns.find((x) => x.name === c.column)?.dtype;
  const out: RuleCondition = { column: c.column, op: c.op };
  const shape = valueShape(c.op);
  if (shape === "one") out.value = literal(c.value, dtype);
  if (shape === "two") { out.value = literal(c.value, dtype); out.value2 = literal(c.value2, dtype); }
  if (shape === "list") out.value = c.value.split(",").map((s) => s.trim()).filter(Boolean).map((s) => literal(s, dtype));
  if (shape === "formula") out.formula = c.formula;
  if (dtype === "text" && isTextOp(c.op) && c.ignoreCase) out.ignore_case = true;
  return out;
}

export function fromCondition(c: RuleCondition): DraftCondition {
  const asText = (v: unknown) => (v === null || v === undefined ? "" : String(v));
  return {
    column: c.column, op: c.op,
    value: Array.isArray(c.value) ? c.value.map(asText).join(", ") : asText(c.value),
    value2: asText(c.value2), ignoreCase: !!c.ignore_case, formula: c.formula ?? [],
  };
}

export type RuleAction = "remove_rows" | "keep_rows" | "set_missing" | "set_value" | "clamp" | "calculate";

export const ACTIONS: { value: RuleAction; label: string; needsTarget: boolean; conditionsOptional: boolean }[] = [
  { value: "remove_rows", label: "Remove those rows", needsTarget: false, conditionsOptional: false },
  { value: "keep_rows", label: "Keep only those rows", needsTarget: false, conditionsOptional: false },
  { value: "set_missing", label: "Make a column empty in those rows", needsTarget: true, conditionsOptional: false },
  { value: "set_value", label: "Set a column to a value in those rows", needsTarget: true, conditionsOptional: false },
  { value: "clamp", label: "Keep a number column within limits", needsTarget: true, conditionsOptional: true },
  { value: "calculate", label: "Recalculate a column with a formula", needsTarget: true, conditionsOptional: true },
];

export interface DraftAction {
  action: RuleAction;
  target: string;
  value: string;
  min: string;
  max: string;
  formula: FormulaTerm[];
}

export function actionProblem(a: DraftAction, columns: ColumnInfo[], conditionCount: number): string | null {
  const spec = ACTIONS.find((x) => x.value === a.action)!;
  if (!spec.conditionsOptional && conditionCount === 0) return "Add at least one condition.";
  if (spec.needsTarget && !columns.some((c) => c.name === a.target)) return "Choose the column to change.";
  const target = columns.find((c) => c.name === a.target);
  if (a.action === "set_value") {
    if (a.value.trim() === "") return "Type the new value.";
    if (isNumeric(target?.dtype) && !Number.isFinite(Number(a.value))) return `'${a.target}' is a number column: type a number.`;
  }
  if (a.action === "clamp") {
    if (!isNumeric(target?.dtype)) return "Limits only work on number columns.";
    if (a.min.trim() === "" && a.max.trim() === "") return "Type a minimum, a maximum, or both.";
    if (a.min.trim() !== "" && a.max.trim() !== "" && Number(a.min) > Number(a.max)) return "The minimum is larger than the maximum.";
  }
  if (a.action === "calculate") return formulaProblem(a.formula);
  return null;
}

export function toOperation(conditions: DraftCondition[], combine: "and" | "or", a: DraftAction, columns: ColumnInfo[]): Operation {
  const spec = ACTIONS.find((x) => x.value === a.action)!;
  const params: Record<string, unknown> = { conditions: conditions.map((c) => toCondition(c, columns)), combine, action: a.action };
  if (spec.needsTarget) params.target = a.target;
  const target = columns.find((c) => c.name === a.target);
  if (a.action === "set_value") params.value = literal(a.value, target?.dtype);
  if (a.action === "clamp") {
    if (a.min.trim() !== "") params.min = Number(a.min);
    if (a.max.trim() !== "") params.max = Number(a.max);
  }
  if (a.action === "calculate") params.formula = a.formula;
  return { type: "apply_rule", params };
}

export function newCheckId(): string {
  return `c${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
}
