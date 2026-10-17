import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { IssueCard } from "../components/IssueCard";
import { OperationBuilder } from "../components/OperationBuilder";
import { errorText, useToast } from "../components/Toast";
import { ErrorState, Loading, Note, PageHead, Skeleton, Tile } from "../components/ui";
import { useAfterChange, useDataset, useDatasetId, useRecommendations } from "../hooks/data";
import { ApiError, api } from "../lib/api";
import { cell, num } from "../lib/format";
import type { Cell, Operation, Preview } from "../lib/types";

function Value({ v, tone }: { v: Cell; tone?: "old" | "new" }) {
  if (v === null || v === undefined) return <span className="chip" style={{ fontStyle: "italic", color: "var(--muted)", fontFamily: "var(--sans)" }}>missing</span>;
  const style = tone === "old" ? { background: "var(--danger-soft)", color: "var(--danger)", textDecoration: "line-through" } : tone === "new" ? { background: "var(--accent-soft)", color: "var(--accent-strong)" } : {};
  return <span className="chip" style={{ ...style, whiteSpace: "pre" }}>{cell(v) === "" ? '""' : cell(v)}</span>;
}

function PreviewView({ op, label, onCancel }: { op: Operation; label?: string; onCancel: () => void }) {
  const id = useDatasetId();
  const ds = useDataset(id);
  const version = ds.data?.current_version;
  const after = useAfterChange();
  const navigate = useNavigate();
  const { notify } = useToast();
  const [base] = useState(version); // the version this preview is about; don't re-preview after applying
  const preview = useQuery({
    queryKey: ["preview", id, base ?? version, op],
    queryFn: () => api.preview(id, op),
    enabled: (base ?? version) !== undefined && (base === undefined || base === version),
    staleTime: Infinity,
    retry: false,
  });
  const apply = useMutation({
    mutationFn: () => api.apply(id, op, (base ?? version) as number),
    onSuccess: (res) => {
      after(res.dataset);
      notify(`Applied: ${res.applied.description}. Now on version ${res.dataset.current_version}.`);
      navigate(`/d/${id}/history`);
    },
  });
  const conflict = apply.error instanceof ApiError && apply.error.code === "version_conflict";

  return (
    <>
      <PageHead
        eyebrow={<div className="xs muted strong" style={{ textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 6 }}>Preview · not applied yet</div>}
        title={preview.data?.description ?? label ?? "Preview"}
        actions={<button className="btn ghost" onClick={onCancel}>← Back</button>}
      />
      {preview.isPending ? <Skeleton height={300} /> : preview.isError ? (
        <div className="stack">
          <Note tone="danger" title="This step can't run as set up.">{errorText(preview.error)}</Note>
          <div><button className="btn" onClick={onCancel}>Change it</button></div>
        </div>
      ) : <PreviewBody p={preview.data} />}

      {preview.data && (
        <div className="stack" style={{ marginTop: "auto" }}>
          {apply.isError && (
            conflict ? (
              <Note tone="danger" title="The dataset changed in another tab.">Load the latest version, then preview this step again. <button className="link-btn" onClick={() => { ds.refetch(); onCancel(); }}>Load latest</button></Note>
            ) : <Note tone="danger">{errorText(apply.error)}</Note>
          )}
          <div className="row">
            <button className="btn primary" disabled={apply.isPending || preview.data.effect.no_change} onClick={() => apply.mutate()}>
              {apply.isPending && <span className="spinner" />} Apply · creates version {((base ?? version) ?? 0) + 1}
            </button>
            <button className="btn" onClick={onCancel}>Cancel</button>
            <span className="small muted">You can revert to version {base ?? version} at any time.</span>
          </div>
        </div>
      )}
    </>
  );
}

function PreviewBody({ p }: { p: Preview }) {
  const e = p.effect;
  const changed = p.samples.changed_rows;
  const removed = p.samples.removed_rows;
  const dtypes = Object.entries(e.dtype_changes);
  return (
    <div className="stack" style={{ "--gap": "20px" } as React.CSSProperties}>
      <section className="tiles" aria-label="Effect">
        <Tile small label="Cells changed" value={num(e.cells_changed)} />
        <Tile small label="Rows removed" value={num(e.rows_removed)} tone={e.rows_removed ? "warn" : undefined} sub={e.rows_removed ? `${num(e.rows_after)} rows left` : undefined} />
        <Tile small label="Columns" value={e.columns_before === e.columns_after ? num(e.columns_after) : `${e.columns_before} → ${e.columns_after}`} />
        {dtypes.length > 0 && <Tile small label="Type" value={dtypes.length === 1 ? `${dtypes[0][1].from} → ${dtypes[0][1].to}` : `${dtypes.length} changed`} />}
      </section>
      {e.no_change && <Note tone="neutral" title="Nothing would change.">This step finds nothing to do on the current version.</Note>}
      {p.warnings.map((w) => <Note key={w}>{w}</Note>)}
      {e.columns_removed.length > 0 && <Note tone="neutral">Columns removed: {e.columns_removed.join(", ")}.</Note>}
      {Object.keys(e.columns_renamed).length > 0 && (
        <Note tone="neutral">Renamed: {Object.entries(e.columns_renamed).map(([a, b]) => `${a} → ${b}`).join(", ")}.</Note>
      )}

      {changed && changed.rows.length > 0 && (
        <section aria-label="Changed rows">
          <h2 style={{ marginBottom: 10 }}>Changed rows</h2>
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th style={{ width: 80 }}>Row</th>
                  {changed.columns.map((c) => <th key={c} colSpan={2}>{c} · before → after</th>)}
                </tr>
              </thead>
              <tbody>
                {changed.rows.map((r) => (
                  <tr key={r.row}>
                    <td className="mono muted">{num(r.row)}</td>
                    {changed.columns.map((c, i) => (
                      r.before[i] === r.after[i] || (r.before[i] === null && r.after[i] === null) ? (
                        <td key={c} colSpan={2}><Value v={r.after[i]} /> <span className="xs muted">unchanged</span></td>
                      ) : (
                        <td key={c} colSpan={2}><span className="row" style={{ gap: 8 }}><Value v={r.before[i]} tone="old" /><span className="muted">→</span><Value v={r.after[i]} tone="new" /></span></td>
                      )
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
            {e.cells_changed > changed.rows.length && (
              <div className="small muted" style={{ padding: "10px 16px", borderTop: "1px solid var(--line)", background: "var(--sunken)" }}>
                Showing {changed.rows.length} examples of {num(e.cells_changed)} changed cells.
              </div>
            )}
          </div>
        </section>
      )}

      {removed && removed.rows.length > 0 && (
        <section aria-label="Removed rows">
          <h2 style={{ marginBottom: 10 }}>Rows that would be removed</h2>
          <div className="table-wrap">
            <table className="data">
              <thead><tr><th style={{ width: 80 }}>Row</th>{removed.columns.map((c) => <th key={c}>{c}</th>)}</tr></thead>
              <tbody>
                {removed.rows.map((r) => (
                  <tr key={r.row}>
                    <td className="mono muted">{num(r.row)}</td>
                    {r.values.map((v, i) => <td key={i} className="cell">{v === null ? <span className="null">missing</span> : cell(v)}</td>)}
                  </tr>
                ))}
              </tbody>
            </table>
            {e.rows_removed > removed.rows.length && (
              <div className="small muted" style={{ padding: "10px 16px", borderTop: "1px solid var(--line)", background: "var(--sunken)" }}>
                Showing {removed.rows.length} of {num(e.rows_removed)} rows.
              </div>
            )}
          </div>
        </section>
      )}
    </div>
  );
}

export default function Clean() {
  const id = useDatasetId();
  const ds = useDataset(id);
  const recs = useRecommendations();
  const location = useLocation();
  const navigate = useNavigate();
  const incoming = (location.state as { operation?: Operation; label?: string } | null) ?? null;
  const [current, setCurrent] = useState<{ op: Operation; label?: string } | null>(incoming?.operation ? { op: incoming.operation, label: incoming.label } : null);

  useEffect(() => {
    if (incoming?.operation) {
      setCurrent({ op: incoming.operation, label: incoming.label });
      navigate(location.pathname, { replace: true, state: null }); // a refresh shouldn't re-open the preview
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [incoming?.operation]);

  useEffect(() => window.scrollTo({ top: 0 }), [current]);

  if (current) return <PreviewView op={current.op} label={current.label} onCancel={() => setCurrent(null)} />;
  if (ds.isPending) return <Loading />;
  if (ds.isError) return <ErrorState error={ds.error} retry={() => ds.refetch()} />;

  return (
    <>
      <PageHead title="Clean" sub="Every step is previewed first and saved as a new version. The original file is never changed." />
      <div className="row" style={{ alignItems: "flex-start", gap: 24 }}>
        <section style={{ flex: "3 1 440px", minWidth: 0 }} className="stack" aria-label="Recommended">
          <h2>Recommended steps</h2>
          {recs.isPending ? <Skeleton height={240} /> : recs.isError ? <ErrorState error={recs.error} /> : recs.data.issues.length === 0 ? (
            <Note tone="accent" title="Nothing to fix.">No issues found in version {ds.data.current_version}. You can still build a step on the right.</Note>
          ) : recs.data.issues.map((i) => <IssueCard key={i.id} issue={i} />)}
        </section>
        <section style={{ flex: "2 1 320px", minWidth: 0 }} className="stack" aria-label="Build a step">
          <h2>Build your own step</h2>
          <OperationBuilder key={ds.data.current_version} columns={ds.data.columns} onPreview={(op, label) => setCurrent({ op, label })} />
          <p className="xs muted">Changed your mind later? <Link to={`/d/${id}/history`}>History</Link> can revert any step.</p>
        </section>
      </div>
    </>
  );
}
