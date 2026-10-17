import type { Cell } from "./types";

const nf = new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }); // the user's own locale

/** Numbers for people: separators, no float noise, "—" for missing. */
export function num(value: unknown, digits = 4): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value !== "number") return String(value);
  if (!Number.isFinite(value)) return "—";
  if (Number.isInteger(value)) return value.toLocaleString();
  if (Math.abs(value) >= 1000) return nf.format(value);
  return Number(value.toPrecision(digits)).toString();
}

export function pct(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return `${Number(value.toFixed(digits))}%`;
}

export function r2(value: number | null | undefined): string {
  return value === null || value === undefined || !Number.isFinite(value) ? "—" : value.toFixed(2);
}

export function bytes(size: number | null | undefined): string {
  if (!size) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let v = size;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return i === 0 ? `${v} B` : `${v.toFixed(1)} ${units[i]}`;
}

export function cell(value: Cell): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "number") return num(value, 6);
  return String(value);
}

export function shortDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

export const roleLabel: Record<string, string> = {
  numeric: "numeric", categorical: "categorical", boolean: "true / false", temporal: "date / time",
  identifier: "identifier", free_text: "free text", constant: "constant", empty: "empty",
};

export const gradeLabel: Record<string, string> = { excellent: "Excellent", good: "Good", fair: "Fair", poor: "Poor" };

export function plural(n: number, one: string, many = one + "s"): string {
  return `${num(n)} ${n === 1 ? one : many}`;
}
