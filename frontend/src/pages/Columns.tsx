import { useSearchParams } from "react-router-dom";
import { BarList } from "../components/charts/BarList";
import { ColumnChart } from "../components/charts/ColumnChart";
import { ErrorState, Loading, Note, PageHead, Skeleton, Tile, TypeChip } from "../components/ui";
import { useColumnDetail, useColumns } from "../hooks/data";
import { cell, num, pct, roleLabel } from "../lib/format";
import type { ColumnDetail } from "../lib/types";

function skewWords(skew: number | null | undefined): string | null {
  if (skew === null || skew === undefined) return null;
  const a = Math.abs(skew);
  const side = skew > 0 ? "right (a tail of high values)" : "left (a tail of low values)";
  if (a < 0.5) return "roughly symmetric";
  return a < 1 ? `moderately skewed to the ${side}` : `strongly skewed to the ${side}`;
}

function Detail({ d }: { d: ColumnDetail }) {
  const nonNull = d.rows - d.null_count;
  const stats: [string, string][] = [];
  if (d.numeric) {
    const n = d.numeric, q = n.quantiles;
    stats.push(["Min", num(n.min)], ["25th percentile", num(q.p25)], ["Median", num(n.median)], ["75th percentile", num(q.p75)],
      ["95th percentile", num(q.p95)], ["Max", num(n.max)], ["Sum", num(n.sum)], ["Zeros", num(n.zeros)], ["Negatives", num(n.negatives)],
      ["Skewness", num(n.skew, 3)], ["Kurtosis", num(n.kurtosis, 3)]);
  } else if (d.temporal) {
    stats.push(["Earliest", d.temporal.min], ["Latest", d.temporal.max], ["Span", `${num(d.temporal.span_days)} days`], ["Grouped by", d.temporal.period]);
  } else if (d.text) {
    stats.push(["Shortest", `${d.text.min_length} chars`], ["Average length", `${num(d.text.mean_length)} chars`], ["Longest", `${d.text.max_length} chars`],
      ["Leading/trailing spaces", num(d.text.padded_with_spaces)]);
  } else if (d.boolean) {
    stats.push(["True", num(d.boolean.true)], ["False", num(d.boolean.false)]);
  }

  const tiles = d.numeric ? (
    <>
      <Tile small label="Mean" value={num(d.numeric.mean)} />
      <Tile small label="Median" value={num(d.numeric.median)} />
      <Tile small label="Std dev" value={num(d.numeric.std)} />
    </>
  ) : (
    <>
      <Tile small label="Most common" value={d.top_values?.items[0] ? cell(d.top_values.items[0].value) || "(empty)" : "—"}
        sub={d.top_values?.items[0] ? `${num(d.top_values.items[0].count)} rows · ${pct(d.top_values.items[0].pct)}` : undefined} />
      <Tile small label="Distinct" value={num(d.top_values?.distinct ?? d.unique_count)} />
    </>
  );
  const out = d.numeric?.outliers;

  return (
    <div className="stack" style={{ "--gap": "20px" } as React.CSSProperties}>
      <div className="row" style={{ gap: 12 }}>
        <h2 style={{ fontSize: 22 }}>{d.name}</h2>
        <TypeChip dtype={d.dtype} />
        <span className="muted">{roleLabel[d.role]}</span>
      </div>
      <section className="tiles" aria-label="Key numbers">
        {tiles}
        <Tile small label="Missing" value={<>{num(d.null_count)} <span style={{ fontSize: 13 }}>{pct(d.null_pct)}</span></>} tone={d.null_count ? "warn" : undefined} />
        <Tile small label="Unique" value={num(d.unique_count)} sub={pct(d.unique_pct)} />
      </section>

      <div className="row" style={{ alignItems: "flex-start", gap: 24 }}>
        <section className="chart-box" style={{ flex: "3 1 420px", minWidth: 0 }} aria-label="Distribution">
          <div className="spread" style={{ alignItems: "baseline", marginBottom: 14 }}>
            <h3>{d.histogram ? "Distribution" : d.temporal ? "Rows over time" : "Most common values"}</h3>
            <span className="xs muted">{num(nonNull)} non-empty values</span>
          </div>
          {d.histogram && d.histogram.length > 0 ? (
            <ColumnChart data={d.histogram.map((b) => ({ label: `${num(b.start)} to ${num(b.end)}`, value: b.count }))}
              edgeLabels={[num(d.histogram[0].start), num(d.histogram[d.histogram.length - 1].end)]} />
          ) : d.temporal && d.temporal.timeline.length > 0 ? (
            <ColumnChart data={d.temporal.timeline.map((p) => ({ label: p.period, value: p.count, tick: p.period }))}
              edgeLabels={[d.temporal.timeline[0].period, d.temporal.timeline[d.temporal.timeline.length - 1].period]} />
          ) : d.top_values && d.top_values.items.length > 0 ? (
            <BarList items={d.top_values.items.map((i) => ({ label: cell(i.value), value: i.count, pct: i.pct }))} other={d.top_values.other_count} />
          ) : <p className="muted small">No values to chart.</p>}
          {d.numeric && (
            <p className="small muted" style={{ marginTop: 14 }}>
              The distribution is {skewWords(d.numeric.skew)}.{" "}
              {out ? (out.count ? `${num(out.count)} value(s) (${pct(out.pct)}) fall outside ${num(out.lower_bound)} to ${num(out.upper_bound)} (1.5 × IQR).`
                : `No outliers: every value sits inside ${num(out.lower_bound)} to ${num(out.upper_bound)} (1.5 × IQR).`) : ""}
            </p>
          )}
        </section>
        <section style={{ flex: "2 1 260px", minWidth: 0 }} className="stack" aria-label="Details">
          {stats.length > 0 && (
            <div className="table-wrap">
              <table className="data"><tbody>
                {stats.map(([k, v], i) => (
                  <tr key={k}><td className="muted" style={i === 0 ? { borderTop: "none" } : undefined}>{k}</td>
                    <td className="r mono" style={i === 0 ? { borderTop: "none" } : undefined}>{v}</td></tr>
                ))}
              </tbody></table>
            </div>
          )}
          {d.numeric && d.top_values && (
            <div>
              <h3 style={{ marginBottom: 8 }}>Most common</h3>
              <BarList items={d.top_values.items.slice(0, 6).map((i) => ({ label: cell(i.value), value: i.count, pct: i.pct }))} />
            </div>
          )}
        </section>
      </div>
      {nonNull > 0 && nonNull < 30 && (
        <Note tone="neutral">Only {num(nonNull)} non-empty values: these numbers describe these rows, not a wider population.</Note>
      )}
    </div>
  );
}

export default function Columns() {
  const columns = useColumns();
  const [params, setParams] = useSearchParams();
  const selected = params.get("name") ?? columns.data?.columns[0]?.name ?? null;
  const detail = useColumnDetail(selected);

  if (columns.isPending) return <Loading />;
  if (columns.isError) return <ErrorState error={columns.error} retry={() => columns.refetch()} />;
  const cols = columns.data.columns;

  return (
    <>
      <PageHead title="Columns" sub="Pick a column to see its distribution, spread and most common values." />
      <div className="row" style={{ alignItems: "flex-start", gap: 24 }}>
        <nav aria-label="Columns" className="stack" style={{ flex: "0 1 240px", minWidth: 200, "--gap": "2px" } as React.CSSProperties}>
          {cols.map((c) => (
            <button key={c.name} onClick={() => setParams({ name: c.name })}
              className={`nav-link ${c.name === selected ? "active" : ""}`}
              style={{ border: "none", background: c.name === selected ? undefined : "transparent", cursor: "pointer", textAlign: "left", gap: 8 }}>
              <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{c.name}</span>
              <span className="row" style={{ gap: 6, flexWrap: "nowrap" }}>
                {c.null_count > 0 && <span className="xs warn-text" title={`${pct(c.null_pct)} missing`}>{pct(c.null_pct, 0)}</span>}
                <span className="mono xs muted">{c.dtype}</span>
              </span>
            </button>
          ))}
        </nav>
        <div style={{ flex: "1 1 520px", minWidth: 0 }}>
          {detail.isPending ? <Skeleton height={420} /> : detail.isError ? <ErrorState error={detail.error} retry={() => detail.refetch()} /> : <Detail d={detail.data} />}
        </div>
      </div>
    </>
  );
}
