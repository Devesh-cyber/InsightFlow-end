import { num } from "../../lib/format";
import type { BoxGroup } from "../../lib/types";
import { compact, niceTicks, useTip, useWidth } from "./common";

/** Horizontal box plots, one row per group. */
export function BoxPlot({ groups }: { groups: BoxGroup[] }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const { setTip, node } = useTip();
  const labelW = Math.min(140, width * 0.22), nW = 44, rowH = 52, top = 8, axisH = 26;
  const plotL = labelW + 8, plotR = width - nW - 8, plotW = plotR - plotL;
  const values = groups.flatMap((g) => [g.min, g.max, ...g.outliers]);
  const ticks = niceTicks(Math.min(...values), Math.max(...values));
  const lo = ticks[0], hi = ticks[ticks.length - 1];
  const x = (v: number) => plotL + ((v - lo) / (hi - lo || 1)) * plotW;
  const height = top + groups.length * rowH + axisH;

  return (
    <div ref={ref} style={{ position: "relative" }}>
      <svg className="chart-svg" width={width} height={height} role="img" aria-label="Box plot">
        {ticks.map((t) => (
          <g key={t}>
            <line className="grid" x1={x(t)} x2={x(t)} y1={top} y2={height - axisH} />
            <text className="tick" x={x(t)} y={height - 8} textAnchor="middle">{compact(t)}</text>
          </g>
        ))}
        <text className="tick" x={width} y={top + 2} textAnchor="end" dominantBaseline="hanging">rows</text>
        {groups.map((g, i) => {
          const cy = top + i * rowH + rowH / 2;
          const single = g.q1 === g.q3;
          const tip = `${g.label} · n ${g.n} · min ${num(g.min)} · Q1 ${num(g.q1)} · median ${num(g.median)} · Q3 ${num(g.q3)} · max ${num(g.max)}${g.outlier_count ? ` · ${g.outlier_count} outlier(s)` : ""}`;
          return (
            <g key={g.label + i} onMouseMove={() => setTip({ x: x(g.median), y: cy - 14, text: tip })} onMouseLeave={() => setTip(null)}>
              <rect x={0} y={cy - rowH / 2} width={width} height={rowH} fill="transparent" />
              <text x={0} y={cy + 4} style={{ fontSize: 13, fontWeight: 600, fill: "var(--text)" }}>
                {g.label.length > 18 ? g.label.slice(0, 17) + "…" : g.label}
              </text>
              <line x1={x(g.whisker_low)} x2={x(g.whisker_high)} y1={cy} y2={cy} stroke="var(--whisker)" strokeWidth={2} />
              <line x1={x(g.whisker_low)} x2={x(g.whisker_low)} y1={cy - 8} y2={cy + 8} stroke="var(--whisker)" strokeWidth={2} />
              <line x1={x(g.whisker_high)} x2={x(g.whisker_high)} y1={cy - 8} y2={cy + 8} stroke="var(--whisker)" strokeWidth={2} />
              {single ? (
                <line x1={x(g.median)} x2={x(g.median)} y1={cy - 13} y2={cy + 13} stroke="var(--mark)" strokeWidth={3} strokeLinecap="round" />
              ) : (
                <>
                  <rect x={x(g.q1)} y={cy - 13} width={Math.max(2, x(g.q3) - x(g.q1))} height={26} rx={4}
                    fill="var(--mark-soft)" stroke="var(--mark)" strokeWidth={2} />
                  <line x1={x(g.median)} x2={x(g.median)} y1={cy - 13} y2={cy + 13} stroke="var(--mark)" strokeWidth={3} />
                </>
              )}
              {g.outliers.slice(0, 60).map((o, j) => (
                <circle key={j} cx={x(o)} cy={cy} r={4} fill="var(--panel)" stroke="var(--mark)" strokeWidth={2} />
              ))}
              <text className="tick" x={width} y={cy + 4} textAnchor="end" style={{ fontSize: 13, fill: "var(--text)" }}>{num(g.n)}</text>
            </g>
          );
        })}
        <line className="axis" x1={plotL} x2={plotR} y1={height - axisH} y2={height - axisH} />
      </svg>
      {node}
    </div>
  );
}
