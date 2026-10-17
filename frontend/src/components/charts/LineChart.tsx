import { useState } from "react";
import { num } from "../../lib/format";
import { compact, niceTicks, useWidth } from "./common";

/** One value over time, 2px line, crosshair + tooltip on hover. Gaps (null) break the line. */
export function LineChart({ points, valueLabel, height = 260 }: {
  points: { t: string; value: number | null; extra?: string }[];
  valueLabel: string;
  height?: number;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const left = 52, right = 12, top = 12, bottom = 28;
  const plotW = width - left - right, plotH = height - top - bottom;
  const vals = points.map((p) => p.value).filter((v): v is number => v !== null);
  const ticks = niceTicks(Math.min(...vals, 0), Math.max(...vals, 0) || 1);
  const lo = ticks[0], hi = ticks[ticks.length - 1];
  const sx = (i: number) => left + (points.length === 1 ? plotW / 2 : (i / (points.length - 1)) * plotW);
  const sy = (v: number) => top + plotH - ((v - lo) / (hi - lo || 1)) * plotH;

  const segments: string[] = [];
  let current = "";
  points.forEach((p, i) => {
    if (p.value === null) {
      if (current) segments.push(current);
      current = "";
    } else current += `${current ? "L" : "M"}${sx(i).toFixed(1)},${sy(p.value).toFixed(1)} `;
  });
  if (current) segments.push(current);
  const every = Math.ceil(points.length / Math.max(1, Math.floor(plotW / 90)));
  const h = hover !== null ? points[hover] : null;

  return (
    <div ref={ref} style={{ position: "relative" }}>
      <svg className="chart-svg" width={width} height={height} role="img" aria-label={`Line chart of ${valueLabel} over time`}
        onMouseMove={(e) => {
          const box = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
          const i = Math.round(((e.clientX - box.left - left) / plotW) * (points.length - 1));
          setHover(Math.max(0, Math.min(points.length - 1, i)));
        }}
        onMouseLeave={() => setHover(null)}>
        {ticks.map((t) => (
          <g key={t}>
            <line className="grid" x1={left} x2={width - right} y1={sy(t)} y2={sy(t)} />
            <text className="tick" x={left - 8} y={sy(t) + 4} textAnchor="end">{compact(t)}</text>
          </g>
        ))}
        {points.map((p, i) => i % every === 0 && (
          <text key={i} className="tick" x={sx(i)} y={height - 8} textAnchor="middle">{p.t}</text>
        ))}
        {segments.map((d, i) => <path key={i} d={d} fill="none" stroke="var(--mark)" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />)}
        {points.length <= 40 && points.map((p, i) => p.value !== null && (
          <circle key={i} cx={sx(i)} cy={sy(p.value)} r={3} fill="var(--mark)" stroke="var(--panel)" strokeWidth={2} />
        ))}
        {h && hover !== null && (
          <>
            <line x1={sx(hover)} x2={sx(hover)} y1={top} y2={top + plotH} stroke="var(--whisker)" strokeDasharray="3 3" />
            {h.value !== null && <circle cx={sx(hover)} cy={sy(h.value)} r={5} fill="var(--mark)" stroke="var(--panel)" strokeWidth={2} />}
          </>
        )}
      </svg>
      {h && hover !== null && (
        <div className="tooltip" style={{ left: sx(hover), top: h.value !== null ? sy(h.value) : top }}>
          {h.t}: <b>{num(h.value)}</b> {valueLabel}{h.extra ? ` · ${h.extra}` : ""}
        </div>
      )}
    </div>
  );
}
