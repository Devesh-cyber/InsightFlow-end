import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { BoxPlot } from "../components/charts/BoxPlot";
import { Heatmap } from "../components/charts/Heatmap";
import { LineChart } from "../components/charts/LineChart";
import { Scatter } from "../components/charts/Scatter";
import { errorText } from "../components/Toast";
import { ErrorState, Loading, Note, PageHead, Skeleton, Tile } from "../components/ui";
import { useColumns, useCorrelations, useRelationship } from "../hooks/data";
import { num, r2 } from "../lib/format";
import type { BoxGroup, Relationship } from "../lib/types";
import { ApiError } from "../lib/api";

function Result({ r }: { r: Relationship }) {
  if (r.type === "numeric_numeric") {
    const [a, b] = r.columns;
    return (
      <div className="stack" style={{ "--gap": "18px" } as React.CSSProperties}>
        {r.small_sample && r.warning && <Note title={`Based on only ${r.n} rows with both values.`}>{r.warning.replace(/^Based on only \d+ row\(s\) with both values\.\s*/, "")}</Note>}
        <div className="row" style={{ alignItems: "stretch", gap: 24 }}>
          <section className="chart-box" style={{ flex: "3 1 420px", minWidth: 0 }} aria-label={`${a} vs ${b}`}>
            <div className="spread" style={{ alignItems: "baseline", marginBottom: 10 }}>
              <h3>{a} vs {b}</h3>
              {r.fit && <span className="xs muted row" style={{ gap: 6 }}><span style={{ width: 16, borderTop: "2px dashed var(--whisker)" }} />best-fit line</span>}
            </div>
            <Scatter points={r.scatter.points} xLabel={a} yLabel={b} fit={r.fit} />
            {r.scatter.sampled && <p className="xs muted">Showing a sample of {num(r.scatter.points.length)} of {num(r.n)} points.</p>}
          </section>
          <section className="stack" style={{ flex: "2 1 260px", minWidth: 0 }}>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
              <Tile small label="Pearson r" value={r2(r.pearson)} />
              <Tile small label="Spearman ρ" value={r2(r.spearman)} />
              <Tile small label="R²" value={r2(r.r_squared)} />
              <Tile small label="Rows used" value={num(r.n)} tone={r.small_sample ? "warn" : undefined} />
            </div>
            <p className="small" style={{ lineHeight: 1.6 }}>{r.interpretation}</p>
          </section>
        </div>
      </div>
    );
  }
  if (r.type === "categorical_categorical") {
    const c = r.contingency;
    return (
      <div className="stack" style={{ "--gap": "18px" } as React.CSSProperties}>
        {r.warnings.map((w) => <Note key={w}>{w}</Note>)}
        <div className="tiles">
          <Tile small label="Cramér's V" value={r2(r.cramers_v)} sub={r.strength} />
          <Tile small label="Chi-square p-value" value={r.chi_square.p_value < 0.001 ? "< 0.001" : num(r.chi_square.p_value, 3)} />
          <Tile small label="Rows used" value={num(r.n)} />
        </div>
        <p className="small">{r.interpretation}</p>
        <section className="chart-box" aria-label="Counts table">
          <h3 style={{ marginBottom: 12 }}>How often each pair occurs ({r.columns[0]} × {r.columns[1]})</h3>
          <Heatmap rows={c.row_labels} cols={c.column_labels} values={c.counts} mode="count"
            cellLabel={(row, col, v) => `${row} & ${col}: ${num(v)} rows`} />
        </section>
      </div>
    );
  }
  if (r.type === "numeric_categorical") {
    const groups: BoxGroup[] = r.groups.map((g) => ({
      label: String(g[r.category_column] ?? "(empty)"), n: Number(g.count), min: Number(g.min), q1: Number(g.q1), median: Number(g.median),
      q3: Number(g.q3), max: Number(g.max), whisker_low: Number(g.min), whisker_high: Number(g.max), outlier_count: 0, outliers: [],
    }));
    return (
      <div className="stack" style={{ "--gap": "18px" } as React.CSSProperties}>
        <div className="tiles">
          <Tile small label="η (eta)" value={r2(r.eta)} sub={r.strength} />
          <Tile small label="η² explained" value={`${Math.round(r.eta_squared * 100)}%`} />
          <Tile small label="Rows used" value={num(r.n)} />
        </div>
        <p className="small">{r.interpretation}</p>
        <section className="chart-box" aria-label="By group">
          <h3 style={{ marginBottom: 12 }}>{r.numeric_column} by {r.category_column}</h3>
          <BoxPlot groups={groups} />
        </section>
      </div>
    );
  }
  return (
    <div className="stack" style={{ "--gap": "18px" } as React.CSSProperties}>
      <div className="tiles">
        <Tile small label="Trend (Spearman ρ vs time)" value={r2(r.spearman_vs_time)} />
        <Tile small label="Rows used" value={num(r.n)} />
        <Tile small label="Grouped by" value={r.period} />
      </div>
      <p className="small">{r.interpretation}</p>
      <section className="chart-box" aria-label="Trend">
        <h3 style={{ marginBottom: 12 }}>Average {r.columns[1]} per {r.period}</h3>
        <LineChart points={r.trend.map((p) => ({ t: p.period, value: p.mean, extra: `${p.count} rows` }))} valueLabel="mean" />
      </section>
    </div>
  );
}

export default function Relationships() {
  const columns = useColumns();
  const [method, setMethod] = useState<"pearson" | "spearman">("pearson");
  const corr = useCorrelations(method);
  const [params, setParams] = useSearchParams();
  const cols = columns.data?.columns ?? [];
  const usable = cols.filter((c) => !["identifier", "free_text", "empty", "constant"].includes(c.role));
  const fallbackA = corr.data?.top_pairs[0]?.a ?? usable[0]?.name ?? null;
  const fallbackB = corr.data?.top_pairs[0]?.b ?? usable.find((c) => c.name !== fallbackA)?.name ?? null;
  const a = params.get("a") ?? fallbackA;
  const b = params.get("b") ?? fallbackB;
  const rel = useRelationship(a, b);

  if (columns.isPending) return <Loading />;
  if (columns.isError) return <ErrorState error={columns.error} retry={() => columns.refetch()} />;

  const pick = (key: "a" | "b", value: string) => setParams({ a: key === "a" ? value : a ?? "", b: key === "b" ? value : b ?? "" });
  const refused = rel.error instanceof ApiError && rel.error.status === 422;

  return (
    <>
      <PageHead title="Relationships" sub="How two columns move together, measured by the test that fits their types."
        actions={
          <div className="row" style={{ gap: 8, alignItems: "flex-end" }}>
            <label className="field">Column A
              <select className="select" aria-label="Column A" value={a ?? ""} onChange={(e) => pick("a", e.target.value)}>
                {cols.map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}
              </select>
            </label>
            <span className="muted" style={{ paddingBottom: 10 }}>vs</span>
            <label className="field">Column B
              <select className="select" aria-label="Column B" value={b ?? ""} onChange={(e) => pick("b", e.target.value)}>
                {cols.map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}
              </select>
            </label>
          </div>
        } />

      {a && b && a === b ? <Note tone="neutral">Pick two different columns.</Note>
        : rel.isPending && rel.fetchStatus !== "idle" ? <Skeleton height={360} />
        : refused ? <Note tone="neutral" title="No relationship measure for this pair.">{errorText(rel.error)}</Note>
        : rel.isError ? <ErrorState error={rel.error} retry={() => rel.refetch()} />
        : rel.data ? <Result r={rel.data} /> : null}

      <section className="stack" aria-label="All numeric pairs" style={{ borderTop: "1px solid var(--line)", paddingTop: 24 }}>
        <div className="spread" style={{ alignItems: "center" }}>
          <div>
            <h2>All numeric pairs</h2>
            <p className="small muted">Click a pair to open it above. Pairs with fewer than 30 rows are flagged.</p>
          </div>
          <div className="segmented" role="tablist" aria-label="Correlation method">
            <button role="tab" aria-selected={method === "pearson"} onClick={() => setMethod("pearson")}>Pearson (linear)</button>
            <button role="tab" aria-selected={method === "spearman"} onClick={() => setMethod("spearman")}>Spearman (rank)</button>
          </div>
        </div>
        {corr.isPending ? <Skeleton height={160} /> : corr.isError ? <ErrorState error={corr.error} /> : corr.data.columns.length < 2 ? (
          <Note tone="neutral">{corr.data.note ?? "Need at least two numeric columns."}</Note>
        ) : (
          <div className="row" style={{ alignItems: "flex-start", gap: 24 }}>
            <div style={{ flex: "2 1 300px", minWidth: 0 }} className="table-wrap">
              <table className="data">
                <thead><tr><th>Pair</th><th className="r">{method === "pearson" ? "r" : "ρ"}</th><th>Strength</th><th className="r">Rows</th></tr></thead>
                <tbody>
                  {corr.data.top_pairs.map((p) => (
                    <tr key={p.a + p.b} className="clickable" onClick={() => setParams({ a: p.a, b: p.b })}>
                      <td>{p.a} · {p.b}</td>
                      <td className="r mono">{r2(p.r)}</td>
                      <td className="muted">{p.strength}</td>
                      <td className="r">{p.small_sample ? <span className="badge warn">n {p.n}</span> : <span className="mono">{num(p.n)}</span>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {corr.data.columns.length <= 12 && (
              <div style={{ flex: "3 1 360px", minWidth: 0 }}>
                <Heatmap rows={corr.data.columns} cols={corr.data.columns} values={corr.data.matrix} mode="diverging"
                  cellLabel={(x, y, v) => `${x} · ${y}: ${r2(v)}`} />
                <p className="xs muted" style={{ marginTop: 8 }}>Green = rise together · plum = one rises as the other falls · pale = little or none.</p>
              </div>
            )}
          </div>
        )}
      </section>
    </>
  );
}
