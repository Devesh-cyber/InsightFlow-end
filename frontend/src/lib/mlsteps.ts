// The steps a user can add to a column in the ML-ready stage. Mirrors backend app/engine/mlprep.py.
import type { MLStep } from "./types";

/** What a column holds as it flows through its steps. */
export type Kind = "number" | "text" | "bool" | "date" | "done";

export function kindOf(dtype: string): Kind {
  if (dtype === "integer" || dtype === "float") return "number";
  if (dtype === "boolean") return "bool";
  if (dtype === "date" || dtype === "datetime") return "date";
  return "text";
}

export const STEP_LABEL: Record<MLStep["kind"], string> = {
  fill_missing: "Fill missing values",
  clip: "Clip extreme values",
  transform: "Transform (log, √)",
  scale: "Scale",
  encode: "Encode categories",
  bin: "Bin into groups",
  date_parts: "Split date into parts",
  to_number: "True/false → 1/0",
};

export const STEP_HELP: Record<MLStep["kind"], string> = {
  fill_missing: "Fill empty cells. Learned from the training rows when the split is on.",
  clip: "Limit extreme values to a range (e.g. the 1st–99th percentile).",
  transform: "Reduce skew. log(1 + x) works with zeros.",
  scale: "Put numbers on a common scale. Needed for linear and distance-based models; trees don't need it.",
  encode: "Turn categories into numbers: one column per value (one-hot), how often it occurs (frequency), or a rank (ordinal).",
  bin: "Group numbers into ranges, e.g. age → 5 age groups (0…4).",
  date_parts: "Replace a date by numbers models can use: year, month, weekday…",
  to_number: "true → 1, false → 0.",
};

/** Steps that make sense for a column of this kind. */
export function stepsFor(kind: Kind): MLStep["kind"][] {
  switch (kind) {
    case "number": return ["fill_missing", "clip", "transform", "scale", "bin", "encode"];
    case "text": return ["fill_missing", "encode"];
    case "bool": return ["fill_missing", "to_number"];
    case "date": return ["date_parts"];
    default: return [];
  }
}

export function defaultStep(kind: MLStep["kind"], current: Kind): MLStep {
  switch (kind) {
    case "fill_missing": return { kind, strategy: current === "number" ? "median" : "mode", add_indicator: false };
    case "clip": return { kind, method: "percentile", lower: 1, upper: 99 };
    case "transform": return { kind, method: "log1p" };
    case "scale": return { kind, method: "standard" };
    case "encode": return { kind, method: "onehot", max_categories: 20, drop_first: false };
    case "bin": return { kind, method: "quantile", bins: 5 };
    case "date_parts": return { kind, parts: ["year", "month", "weekday"] };
    case "to_number": return { kind };
  }
}

/** The column's kind after a step. */
export function after(step: MLStep, current: Kind): Kind {
  if (step.kind === "date_parts") return "done";
  if (step.kind === "encode") return step.method === "onehot" ? "done" : "number";
  if (step.kind === "to_number") return "number";
  if (step.kind === "fill_missing" && step.strategy === "constant") return current;
  return current;
}

const METHOD_TEXT: Record<string, string> = {
  mean: "mean", median: "median", mode: "most common", constant: "a value",
  percentile: "percentiles", iqr: "IQR fences", values: "fixed values",
  log1p: "log(1 + x)", log: "ln(x)", log10: "log10(x)", sqrt: "√x", square: "x²",
  standard: "standard (z-score)", minmax: "min-max (0–1)", robust: "robust (median/IQR)",
  onehot: "one-hot", frequency: "frequency", ordinal: "ordinal",
  quantile: "equal-count", equal_width: "equal-width",
};

export function describe(step: MLStep): string {
  switch (step.kind) {
    case "fill_missing": return `fill ${step.strategy === "constant" ? `"${String(step.value ?? "")}"` : METHOD_TEXT[step.strategy]}${step.add_indicator ? " + flag" : ""}`;
    case "clip": return step.method === "percentile" ? `clip ${step.lower ?? 1}–${step.upper ?? 99}th pct` : step.method === "iqr" ? `clip IQR×${step.factor ?? 1.5}` : `clip ${step.lower ?? "…"}–${step.upper ?? "…"}`;
    case "transform": return METHOD_TEXT[step.method];
    case "scale": return `scale ${METHOD_TEXT[step.method].split(" ")[0]}`;
    case "encode": return METHOD_TEXT[step.method];
    case "bin": return `${step.bins} ${METHOD_TEXT[step.method]} bins`;
    case "date_parts": return step.parts.join(", ");
    case "to_number": return "1/0";
  }
}

export const DATE_PARTS = ["year", "month", "day", "weekday", "dayofyear", "is_weekend", "hour"];
