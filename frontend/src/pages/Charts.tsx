import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { BarList } from "../components/charts/BarList";
import { BoxPlot } from "../components/charts/BoxPlot";
import { ColumnChart } from "../components/charts/ColumnChart";
import { Heatmap } from "../components/charts/Heatmap";
import { LineChart } from "../components/charts/LineChart";
import { Scatter } from "../components/charts/Scatter";
import { ErrorState, Loading, Note, PageHead, Skeleton } from "../components/ui";
import { useChartData, useChartOptions, useColumns } from "../hooks/data";
import { num } from "../lib/format";
import type { ChartData, ChartType } from "../lib/types";

const AGGS = ["count", "sum", "mean", "median", "min", "max"];
const NAMES: Record<ChartType, string> = { histogram: "Histogram", box: "Box", bar: "Bar", line: "Line", scatter: "Scatter", heatmap: "Heatmap" };

function Table({ d }: { d: ChartData }) {
  let head: string[] = [], rows: (string | number)[][] = [];
  if (d.type === "histogram") { head = ["From", "To", "Rows"]; rows = d.bins.map((b) => [num(b.start), num(b.end), num(b.count)]); }
  if (d.type === "bar") { head = ["Category", d.y ? `${d.agg} of ${d.y}` : "Rows", "Rows used"]; rows = d.bars.map((b) => [b.label, num(b.value), num(b.rows)]); }
  if (d.type === "box") { head = ["Group", "n", "Min", "Q1", "Median", "Q3", "Max", "Outliers"]; rows = d.groups.map((g) => [g.label, g.n, num(g.min), num(g.q1), num(g.median), num(g.q3), num(g.max), g.outlier_count]); }
  if (d.type === "line") { head = ["Period", d.y ? `${d.agg} of ${d.y}` : "Rows"]; rows = d.points.map((p) => [p.t, num(p.value)]); }
  if (d.type === "scatter") { head = [d.x, d.y]; rows = d.points.slice(0, 500).map((p) => [num(p[0]), num(p[1])]); }
  if (d.type === "heatmap") { head = [`${d.y} \\ ${d.x}`, ...d.x_labels]; rows = d.y_labels.map((y, i) => [y, ...d.counts[i].map((c) => num(c))]); }
  return (
    <div className="table-wrap" style={{ maxHeight: 460 }}>
      <table className="data">
        <thead><tr>{head.map((h, i) => <th key={i} className={i ? "r" : ""}>{h}</th>)}</tr></thead>
        <tbody>{rows.map((r, i) => <tr key={i}>{r.map((c, j) => <td key={j} className={j ? "r mono" : ""}>{c}</td>)}</tr>)}</tbody>
      </table>
    </div>
  );
}

function Chart({ d }: { d: ChartData }) {
  switch (d.type) {
    case "histogram":
      return <ColumnChart data={d.bins.map((b) => ({ label: `${num(b.start)} to ${num(b.end)}`, value: b.count }))}
        edgeLabels={[num(d.bins[0]?.start), num(d.bins[d.bins.length - 1]?.end)]} height={280} />;
    case "box":
      return <BoxPlot groups={d.groups} />;
    case "bar":
      return <BarList items={d.bars.map((b) => ({ label: b.label, value: b.value }))} />;
    case "line":
      return <LineChart points={d.points} valueLabel={d.y ? `${d.agg} of ${d.y}` : "rows"} />;
    case "scatter":
      return <Scatter points={d.points} xLabel={d.x} yLabel={d.y} height={340} />;
    case "heatmap":
      return <Heatmap rows={d.y_labels} cols={d.x_labels} values={d.counts} mode="count" cellLabel={(r, c, v) => `${r} & ${c}: ${num(v)} rows`} />;
  }
}

function caption(d: ChartData): string {
  if (d.type === "box") return `${d.groups.reduce((s, g) => s + g.n, 0).toLocaleString()} values${d.truncated ? " · largest groups only" : ""}`;
  if (d.type === "scatter") return d.sampled ? `Sample of ${num(d.points.length)} of ${num(d.total_points)} points` : `${num(d.points.length)} points`;
  if (d.type === "bar") return d.categories_total > d.bars.length ? `Top ${d.bars.length} of ${num(d.categories_total)} categories` : `${d.bars.length} categories`;
  if (d.type === "line") return `Grouped by ${d.period}`;
  if (d.type === "heatmap") return d.truncated ? "Most common categories only" : "";
  return `${d.bins.length} bins`;
}

export default function Charts() {
  const columns = useColumns();
  const [params, setParams] = useSearchParams();
  const cols = columns.data?.columns ?? [];
  const x = params.get("x") ?? cols.find((c) => c.role === "numeric")?.name ?? cols[0]?.name ?? null;
  const y = params.get("y") || null;
  const options = useChartOptions(x, y);
  const [type, setType] = useState<ChartType | "table" | null>(null);
  const [agg, setAgg] = useState("mean");
  const available = options.data?.charts ?? [];
  const chartType: ChartType | null = type && type !== "table" && available.some((c) => c.type === type) ? type : available[0]?.type ?? null;

  useEffect(() => setType(null), [x, y]);
  const needsAgg = !!y && (chartType === "bar" || chartType === "line");
  const data = useChartData(chartType && x ? { type: chartType, x, y: y ?? undefined, agg: needsAgg ? agg : undefined } : null);

  if (columns.isPending) return <Loading />;
  if (columns.isError) return <ErrorState error={columns.error} retry={() => columns.refetch()} />;

  const set = (key: "x" | "y", value: string) => {
    const next = { x: x ?? "", y: y ?? "", [key]: value };
    setParams(Object.fromEntries(Object.entries(next).filter(([, v]) => v)));
  };

  return (
    <>
      <PageHead title="Charts" sub="Pick columns. InsightFlow suggests the chart that fits their types." />
      <div className="row" style={{ alignItems: "flex-end", gap: 12 }}>
        <label className="field">Column
          <select className="select" aria-label="Column" value={x ?? ""} onChange={(e) => set("x", e.target.value)} style={{ minWidth: 170 }}>
            {cols.map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}
          </select>
        </label>
        <label className="field">Compare with (optional)
          <select className="select" aria-label="Compare with" value={y ?? ""} onChange={(e) => set("y", e.target.value)} style={{ minWidth: 170 }}>
            <option value="">— none —</option>
            {cols.filter((c) => c.name !== x).map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}
          </select>
        </label>
        {needsAgg && (
          <label className="field">Value
            <select className="select" aria-label="Value" value={agg} onChange={(e) => setAgg(e.target.value)}>
              {AGGS.map((a) => <option key={a} value={a}>{a} of {y}</option>)}
            </select>
          </label>
        )}
        {available.length > 0 && (
          <div className="segmented" role="tablist" aria-label="Chart type" style={{ marginLeft: "auto" }}>
            {available.map((c) => (
              <button key={c.type} role="tab" aria-selected={type !== "table" && chartType === c.type} title={c.description} onClick={() => setType(c.type)}>
                {NAMES[c.type]}{c.recommended ? " · suggested" : ""}
              </button>
            ))}
            <button role="tab" aria-selected={type === "table"} onClick={() => setType("table")}>Table</button>
          </div>
        )}
      </div>

      {options.isPending ? <Skeleton height={320} /> : options.isError ? <ErrorState error={options.error} /> : available.length === 0 ? (
        <Note tone="neutral" title="No standard chart fits these columns.">{options.data.reason} Try a different pair: identifiers and free text can't be charted meaningfully.</Note>
      ) : data.isPending ? <Skeleton height={320} /> : data.isError ? <ErrorState error={data.error} retry={() => data.refetch()} /> : (
        <section className="chart-box" aria-label="Chart">
          <div className="spread" style={{ alignItems: "baseline", marginBottom: 16 }}>
            <h2>{y ? `${x} by ${y}` : x}</h2>
            <span className="xs muted">{available.find((c) => c.type === chartType)?.description} · {caption(data.data)}</span>
          </div>
          {type === "table" ? <Table d={data.data} /> : <Chart d={data.data} />}
        </section>
      )}
      <p className="xs muted">Hover a bar, box or point for exact numbers. The Table tab shows the same figures.</p>
    </>
  );
}
