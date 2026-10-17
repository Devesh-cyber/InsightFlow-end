import { num } from "../../lib/format";
import { compact, niceTicks, useTip, useWidth } from "./common";

/** Scatter plot with an optional best-fit line. Overlapping points are counted, not hidden. */
export function Scatter({ points, xLabel, yLabel, fit, height = 300 }: {
  points: [number, number][];
  xLabel: string;
  yLabel: string;
  fit?: { slope: number; intercept: number } | null;
  height?: number;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const { setTip, node } = useTip();
  const left = 56, right = 12, top = 12, bottom = 40;
  const plotW = width - left - right, plotH = height - top - bottom;
  const xs = points.map((p) => p[0]), ys = points.map((p) => p[1]);
  const xt = niceTicks(Math.min(...xs), Math.max(...xs), 5);
  const yt = niceTicks(Math.min(...ys), Math.max(...ys), 4);
  const [x0, x1] = [xt[0], xt[xt.length - 1]], [y0, y1] = [yt[0], yt[yt.length - 1]];
  const sx = (v: number) => left + ((v - x0) / (x1 - x0 || 1)) * plotW;
  const sy = (v: number) => top + plotH - ((v - y0) / (y1 - y0 || 1)) * plotH;

  // Group identical points so "2 rows" is visible instead of hidden behind one dot.
  const grouped = new Map<string, { x: number; y: number; n: number }>();
  for (const [px, py] of points) {
    const k = `${px}|${py}`;
    const g = grouped.get(k);
    if (g) g.n++;
    else grouped.set(k, { x: px, y: py, n: 1 });
  }
  const r = points.length > 2000 ? 2.5 : points.length > 300 ? 3.5 : 5;

  let line: [number, number, number, number] | null = null;
  if (fit) {
    const clampY = (v: number) => Math.min(y1, Math.max(y0, v));
    const a = clampY(fit.slope * x0 + fit.intercept), b = clampY(fit.slope * x1 + fit.intercept);
    const xa = fit.slope ? (a - fit.intercept) / fit.slope : x0, xb = fit.slope ? (b - fit.intercept) / fit.slope : x1;
    line = [sx(Math.max(x0, Math.min(xa, x1))), sy(a), sx(Math.max(x0, Math.min(xb, x1))), sy(b)];
  }

  return (
    <div ref={ref} style={{ position: "relative" }}>
      <svg className="chart-svg" width={width} height={height} role="img" aria-label={`Scatter plot of ${yLabel} against ${xLabel}`}>
        {yt.map((t) => (
          <g key={"y" + t}>
            <line className="grid" x1={left} x2={width - right} y1={sy(t)} y2={sy(t)} />
            <text className="tick" x={left - 8} y={sy(t) + 4} textAnchor="end">{compact(t)}</text>
          </g>
        ))}
        {xt.map((t) => (
          <text key={"x" + t} className="tick" x={sx(t)} y={height - 22} textAnchor="middle">{compact(t)}</text>
        ))}
        <line className="axis" x1={left} x2={width - right} y1={top + plotH} y2={top + plotH} />
        <line className="axis" x1={left} x2={left} y1={top} y2={top + plotH} />
        {line && <line x1={line[0]} y1={line[1]} x2={line[2]} y2={line[3]} stroke="var(--whisker)" strokeWidth={2} strokeDasharray="6 5" />}
        {[...grouped.values()].map((p, i) => (
          <circle key={i} cx={sx(p.x)} cy={sy(p.y)} r={p.n > 1 && points.length < 300 ? r + 1.5 : r} className="mark"
            fillOpacity={points.length > 300 ? 0.55 : 1} stroke="var(--panel)" strokeWidth={points.length > 300 ? 0 : 2}
            onMouseMove={() => setTip({ x: sx(p.x), y: sy(p.y) - 6, text: <>{xLabel} {num(p.x)} · {yLabel} {num(p.y)}{p.n > 1 ? ` · ${p.n} rows` : ""}</> })}
            onMouseLeave={() => setTip(null)} />
        ))}
        <text className="tick" x={left + plotW / 2} y={height - 4} textAnchor="middle" style={{ fontFamily: "var(--sans)", fontSize: 12 }}>{xLabel}</text>
        <text className="tick" transform={`translate(12 ${top + plotH / 2}) rotate(-90)`} textAnchor="middle" style={{ fontFamily: "var(--sans)", fontSize: 12 }}>{yLabel}</text>
      </svg>
      {node}
    </div>
  );
}
