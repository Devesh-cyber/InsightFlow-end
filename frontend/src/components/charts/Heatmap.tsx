import { num, r2 } from "../../lib/format";

/**
 * Grid of cells. "count" = one hue, light → dark (magnitude).
 * "diverging" = correlation: rust (negative) ↔ neutral ↔ green (positive).
 */
export function Heatmap({ rows, cols, values, mode, cellLabel }: {
  rows: string[];
  cols: string[];
  values: (number | null)[][];
  mode: "count" | "diverging";
  cellLabel?: (r: string, c: string, v: number | null) => string;
}) {
  const flat = values.flat().filter((v): v is number => v !== null);
  const peak = Math.max(1, ...flat.map(Math.abs));
  const bg = (v: number | null) => {
    if (v === null) return "var(--sunken)";
    if (mode === "count") return `color-mix(in oklab, var(--mark) ${Math.round(8 + 82 * (v / peak))}%, var(--panel))`;
    const strength = Math.round(85 * Math.min(1, Math.abs(v)));
    return `color-mix(in oklab, ${v >= 0 ? "var(--mark)" : "var(--neg)"} ${strength}%, var(--panel))`;
  };
  const dark = (v: number | null) => v !== null && (mode === "count" ? v / peak > 0.55 : Math.abs(v) > 0.6);

  return (
    <div className="table-wrap" style={{ border: "none" }}>
      <table className="data" style={{ width: "auto", borderCollapse: "separate", borderSpacing: 2 }}>
        <thead>
          <tr>
            <th />
            {cols.map((c) => <th key={c} className="r" style={{ fontSize: 12, maxWidth: 120, overflow: "hidden", textOverflow: "ellipsis" }} title={c}>{c}</th>)}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={r}>
              <th style={{ fontSize: 12, textAlign: "right", maxWidth: 160, overflow: "hidden", textOverflow: "ellipsis" }} title={r}>{r}</th>
              {cols.map((c, j) => {
                const v = values[i]?.[j] ?? null;
                return (
                  <td key={c} className="r mono" title={cellLabel?.(r, c, v)}
                    style={{ background: bg(v), color: dark(v) ? "var(--on-primary)" : "var(--text)", borderTop: "none", borderRadius: 6, minWidth: 56, padding: "10px 12px" }}>
                    {v === null ? "—" : mode === "count" ? num(v) : r2(v)}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
