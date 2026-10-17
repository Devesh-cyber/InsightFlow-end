import { useState } from "react";
import { useRows } from "../hooks/data";
import { cell, num } from "../lib/format";
import { ErrorState, Skeleton } from "./ui";

const PAGE = 25;

/** The actual rows, a page at a time: the evidence behind every number. */
export function RowsTable() {
  const [offset, setOffset] = useState(0);
  const rows = useRows(offset, PAGE);
  if (rows.isPending) return <Skeleton height={240} />;
  if (rows.isError) return <ErrorState error={rows.error} retry={() => rows.refetch()} />;
  const d = rows.data;
  const last = Math.min(d.offset + d.rows.length, d.total_rows);
  return (
    <div className="stack">
      <div className="table-wrap" style={{ maxHeight: 520 }}>
        <table className="data">
          <thead>
            <tr>
              <th className="r" style={{ width: 56 }}>#</th>
              {d.columns.map((c) => (
                <th key={c.name} title={c.dtype} className={c.dtype === "integer" || c.dtype === "float" ? "r" : ""}>{c.name} <span className="mono xs" style={{ fontWeight: 400 }}>{c.dtype}</span></th>
              ))}
            </tr>
          </thead>
          <tbody style={{ opacity: rows.isPlaceholderData ? 0.5 : 1 }}>
            {d.rows.map((r, i) => (
              <tr key={d.offset + i}>
                <td className="r mono xs muted">{num(d.offset + i + 1)}</td>
                {r.map((v, j) => (
                  <td key={j} className={`cell ${typeof v === "number" ? "r" : ""}`} title={v === null ? "missing" : String(v)}>
                    {v === null ? <span className="null">missing</span> : cell(v)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="spread" style={{ alignItems: "center" }}>
        <span className="xs muted mono">Rows {num(d.offset + 1)}–{num(last)} of {num(d.total_rows)}</span>
        <div className="row" style={{ gap: 6 }}>
          <button className="btn sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>← Previous</button>
          <button className="btn sm" disabled={last >= d.total_rows} onClick={() => setOffset(offset + PAGE)}>Next →</button>
        </div>
      </div>
    </div>
  );
}
