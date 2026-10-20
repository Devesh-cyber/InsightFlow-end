import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useDataset, useDatasetId } from "../hooks/data";
import { api } from "../lib/api";
import { cell, num } from "../lib/format";
import type { RowMatch } from "../lib/types";
import { errorText } from "./Toast";
import { Skeleton } from "./ui";

const PAGE = 20;

/**
 * The actual rows an issue, rule or check is about, straight from the current version.
 * The columns it's about come first and are highlighted; the rest follow for context.
 */
export function MatchingRowsTable({ match, emptyText }: { match: RowMatch; emptyText?: string }) {
  const id = useDatasetId();
  const ds = useDataset(id);
  const version = ds.data?.current_version;
  const [shown, setShown] = useState(PAGE);
  const q = useQuery({
    queryKey: ["ds", id, version, "match", match, shown],
    queryFn: () => api.matchingRows(id, match, 0, shown, version),
    enabled: version !== undefined,
    staleTime: Infinity,
    retry: false,
    placeholderData: keepPreviousData,
  });

  if (q.isPending) return <Skeleton height={120} />;
  if (q.isError) return <div className="small danger-text">{errorText(q.error)}</div>;
  const d = q.data;
  if (d.total === 0) return <div className="small muted">{emptyText ?? `No rows match in version ${d.version}.`}</div>;

  // highlighted columns first, then the rest in their usual order
  const order = [...d.highlight.map((c) => d.columns.indexOf(c)).filter((i) => i >= 0),
    ...d.columns.map((_, i) => i).filter((i) => !d.highlight.includes(d.columns[i]))];
  const hl = new Set(d.highlight);

  return (
    <div className="stack" style={{ "--gap": "6px" } as React.CSSProperties}>
      <div className="xs muted">
        {num(d.total)} of {num(d.rows_in_version)} rows in version {d.version}
        {d.highlight.length > 0 && <> · highlighted: {d.highlight.join(", ")}</>}
        {d.highlight.length === 0 && <> · identical rows are shown next to each other</>}
      </div>
      <div className="table-wrap" style={{ maxHeight: 360, overflow: "auto" }}>
        <table className="data">
          <thead>
            <tr>
              <th style={{ width: 64 }}>Row</th>
              {order.map((i) => <th key={d.columns[i]} className={hl.has(d.columns[i]) ? "hl" : undefined}>{d.columns[i]}</th>)}
            </tr>
          </thead>
          <tbody>
            {d.rows.map((r) => (
              <tr key={r.row}>
                <td className="mono muted">{num(r.row)}</td>
                {order.map((i) => {
                  const v = r.values[i];
                  return (
                    <td key={d.columns[i]} className={`cell${hl.has(d.columns[i]) ? " hl" : ""}`} style={{ whiteSpace: "pre" }}>
                      {v === null ? <span className="null">missing</span> : cell(v) === "" ? '""' : cell(v)}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {d.total > d.rows.length && (
        <div className="row" style={{ gap: 12 }}>
          <span className="xs muted">Showing {num(d.rows.length)} of {num(d.total)}.</span>
          {shown < 100 && (
            <button type="button" className="link-btn" disabled={q.isFetching} onClick={() => setShown((s) => Math.min(100, s + 40))}>
              {q.isFetching ? "Loading…" : "Show more"}
            </button>
          )}
        </div>
      )}
    </div>
  );
}

/** A "Show rows" toggle that opens the table inline. */
export function ShowRows({ match, label = "Show rows", emptyText }: { match: RowMatch; label?: string; emptyText?: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="stack" style={{ "--gap": "8px" } as React.CSSProperties}>
      <button type="button" className="link-btn" style={{ alignSelf: "flex-start" }} aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        {open ? "Hide rows ▴" : `${label} ▾`}
      </button>
      {open && <MatchingRowsTable match={match} emptyText={emptyText} />}
    </div>
  );
}
