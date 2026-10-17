import { num, pct } from "../../lib/format";

/** Horizontal bars for categories: label, bar, value. Easy to read with long labels. */
export function BarList({ items, other, valueSuffix }: {
  items: { label: string; value: number; pct?: number | null }[];
  other?: number;
  valueSuffix?: string;
}) {
  const peak = Math.max(1e-9, ...items.map((i) => Math.abs(i.value)));
  return (
    <div className="stack" style={{ "--gap": "8px" } as React.CSSProperties} role="list">
      {items.map((item, i) => (
        <div className="hbar" key={i} role="listitem" title={`${item.label}: ${num(item.value)}${item.pct != null ? ` (${pct(item.pct)})` : ""}`}>
          <span className="hbar-label">{item.label === "" ? <span className="null">(empty)</span> : item.label}</span>
          <span className="hbar-track"><span className="hbar-fill" style={{ width: `${(100 * Math.abs(item.value)) / peak}%` }} /></span>
          <span className="hbar-value">{num(item.value)}{valueSuffix}</span>
        </div>
      ))}
      {!!other && <span className="xs muted">+ {num(other)} rows in other values</span>}
    </div>
  );
}
