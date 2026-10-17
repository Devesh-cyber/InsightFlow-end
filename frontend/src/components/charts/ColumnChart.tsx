import { num } from "../../lib/format";
import { barPath, compact, niceTicks, useTip, useWidth } from "./common";

export interface Column {
  label: string;      // full label for the tooltip
  value: number;
  tick?: string;      // short label for the axis (optional)
}

/** Vertical bars: histograms, timelines, aggregated bars. One series, so one colour. */
export function ColumnChart({ data, height = 220, valueLabel = "rows", edgeLabels }: {
  data: Column[];
  height?: number;
  valueLabel?: string;
  edgeLabels?: [string, string];
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const { setTip, node } = useTip();
  const left = 44, right = 8, top = 10, bottom = 26;
  const plotW = width - left - right;
  const plotH = height - top - bottom;
  const max = Math.max(0, ...data.map((d) => d.value));
  const min = Math.min(0, ...data.map((d) => d.value));
  const ticks = niceTicks(min, max || 1);
  const lo = ticks[0], hi = ticks[ticks.length - 1];
  const y = (v: number) => top + plotH - ((v - lo) / (hi - lo || 1)) * plotH;
  const slot = plotW / Math.max(data.length, 1);
  const gap = Math.min(6, slot * 0.2);
  const showEvery = Math.ceil(data.length / Math.max(1, Math.floor(plotW / 70)));

  return (
    <div ref={ref} style={{ position: "relative" }}>
      <svg className="chart-svg" width={width} height={height} role="img" aria-label={`Bar chart, ${data.length} bars`}>
        {ticks.map((t) => (
          <g key={t}>
            <line className="grid" x1={left} x2={width - right} y1={y(t)} y2={y(t)} />
            <text className="tick" x={left - 8} y={y(t) + 4} textAnchor="end">{compact(t)}</text>
          </g>
        ))}
        {data.map((d, i) => {
          const x = left + i * slot + gap / 2;
          const w = Math.max(1, slot - gap);
          const y0 = y(Math.max(0, d.value)), h = Math.abs(y(0) - y(d.value));
          return (
            <g key={i}>
              <rect
                x={left + i * slot} y={top} width={slot} height={plotH} fill="transparent"
                onMouseMove={() => setTip({ x: x + w / 2, y: y0, text: <>{d.label}: <b>{num(d.value)}</b> {valueLabel}</> })}
                onMouseLeave={() => setTip(null)}
              />
              <path className="mark" d={barPath(x, y0, w, h)} pointerEvents="none" />
              {!edgeLabels && d.tick !== undefined && i % showEvery === 0 && (
                <text className="tick" x={x + w / 2} y={height - 8} textAnchor="middle">{d.tick}</text>
              )}
            </g>
          );
        })}
        <line className="axis" x1={left} x2={width - right} y1={y(0)} y2={y(0)} />
        {edgeLabels && (
          <>
            <text className="tick" x={left} y={height - 8}>{edgeLabels[0]}</text>
            <text className="tick" x={width - right} y={height - 8} textAnchor="end">{edgeLabels[1]}</text>
          </>
        )}
      </svg>
      {node}
    </div>
  );
}
