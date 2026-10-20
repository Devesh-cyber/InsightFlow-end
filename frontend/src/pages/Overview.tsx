import { useMutation } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router-dom";
import { IssueCard } from "../components/IssueCard";
import { RowsTable } from "../components/RowsTable";
import { useToast } from "../components/Toast";
import { ErrorState, Loading, Note, PageHead, Tile, TypeChip } from "../components/ui";
import { useAfterChange, useColumns, useDataset, useDatasetId, useHealth, useOverview } from "../hooks/data";
import { api } from "../lib/api";
import { gradeLabel, num, pct, roleLabel } from "../lib/format";

function SheetSwitch() {
  const id = useDatasetId();
  const ds = useDataset(id).data;
  const after = useAfterChange();
  const { fail, notify } = useToast();
  const m = useMutation({
    mutationFn: (sheet: string) => api.selectSheet(id, sheet, ds!.current_version),
    onSuccess: (d) => { after(d); notify(`Loaded sheet '${d.sheet_name}' as version ${d.current_version}.`); },
    onError: fail,
  });
  if (!ds?.sheet_names || ds.sheet_names.length < 2) return null;
  return (
    <label className="field" style={{ minWidth: 180 }}>Sheet
      <select className="select" aria-label="Sheet" value={ds.sheet_name ?? ""} disabled={m.isPending} onChange={(e) => m.mutate(e.target.value)}>
        {ds.sheet_names.map((s) => <option key={s} value={s}>{s}</option>)}
      </select>
    </label>
  );
}

export default function Overview() {
  const id = useDatasetId();
  const ds = useDataset(id);
  const overview = useOverview();
  const health = useHealth();
  const columns = useColumns();
  const navigate = useNavigate();

  if (ds.isError) return <ErrorState error={ds.error} retry={() => ds.refetch()} />;
  if (ds.isPending || overview.isPending) return <Loading />;
  if (overview.isError) return <ErrorState error={overview.error} retry={() => overview.refetch()} />;

  const d = ds.data, o = overview.data;
  const ingest = d.ingest_report;
  // the start page only shows primary cleaning; secondary items wait for the Explore stage
  const issues = (health.data?.issues ?? []).filter((i) => (i.stage ?? "primary") === "primary" && !i.reviewed);
  const secondaryOpen = health.data?.stages?.secondary.open ?? 0;
  const top = [...issues].sort((a, b) => (a.severity === b.severity ? 0 : a.severity === "critical" ? -1 : a.severity === "warning" && b.severity === "info" ? -1 : 1)).slice(0, 3);
  const nullPct = Object.fromEntries((columns.data?.columns ?? []).map((c) => [c.name, c]));

  return (
    <>
      <PageHead
        title="Overview"
        sub={<>{d.source_filename}{d.sheet_name ? ` · sheet ${d.sheet_name}` : ""} · version {d.current_version}</>}
        actions={<><SheetSwitch /><Link to="/" className="btn primary">Upload a file</Link></>}
      />

      <section aria-label="Summary" className="tiles">
        <Tile label="Rows" value={num(o.rows)} />
        <Tile label="Columns" value={num(o.columns)} />
        <Tile label="Completeness" value={pct(o.completeness_pct)} sub={`${num(o.missing_cells)} empty cells`} />
        <Tile label="Duplicate rows" value={num(o.duplicate_rows)} tone={o.duplicate_rows ? "warn" : undefined} />
        {health.data ? (
          <Tile label={`Health · ${gradeLabel[health.data.grade]}`} value={num(health.data.score)} tone={health.data.score >= 80 ? "accent" : "warn"} />
        ) : <Tile label="Health" value="…" />}
      </section>

      {ingest && (ingest.warnings.length > 0 || ingest.renamed_columns.length > 0 || ingest.dropped_empty_rows > 0 || ingest.dropped_empty_columns.length > 0) && (
        <Note tone="neutral" title="While reading the file:">
          {[
            ...ingest.warnings,
            ...(ingest.renamed_columns.length ? [`${ingest.renamed_columns.length} column name(s) were tidied (blank or repeated headers).`] : []),
            ...(ingest.dropped_empty_rows ? [`${num(ingest.dropped_empty_rows)} completely empty row(s) were skipped.`] : []),
            ...(ingest.dropped_empty_columns.length ? [`Empty column(s) skipped: ${ingest.dropped_empty_columns.join(", ")}.`] : []),
          ].join(" ")}
        </Note>
      )}

      <div className="row" style={{ alignItems: "flex-start", gap: 24 }}>
        <section aria-label="Columns" style={{ flex: "3 1 460px", minWidth: 0 }}>
          <h2 style={{ marginBottom: 10 }}>Columns</h2>
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr><th>Name</th><th>Type</th><th>Role</th><th className="r">Missing</th><th className="r">Unique</th></tr>
              </thead>
              <tbody>
                {o.preview.columns.map((c) => {
                  const p = nullPct[c.name];
                  return (
                    <tr key={c.name} className="clickable" onClick={() => navigate(`/d/${id}/columns?name=${encodeURIComponent(c.name)}`)}>
                      <td className="strong">{c.name}</td>
                      <td><TypeChip dtype={c.dtype} /></td>
                      <td className="muted">{p ? roleLabel[p.role] : "…"}</td>
                      <td className={`r ${p && p.null_pct > 0 ? "warn-text" : ""}`}>{p ? pct(p.null_pct) : "…"}</td>
                      <td className="r">{p ? num(p.unique_count) : "…"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>

        <section aria-label="Needs attention" style={{ flex: "2 1 300px", minWidth: 0 }}>
          <h2 style={{ marginBottom: 10 }}>
            Primary cleaning {issues.length > 0 && <span className="badge warn" style={{ marginLeft: 6 }}>{issues.length}</span>}
          </h2>
          {health.isPending ? <div className="skeleton" style={{ height: 200 }} /> : issues.length === 0 ? (
            <Note tone="accent" title="Primary cleaning done ✓">
              Explore the columns and charts next.{secondaryOpen > 0 && <> {secondaryOpen} item(s) (outliers, skew) are waiting in <Link to={`/d/${id}/clean`}>Explore & review</Link>.</>}
            </Note>
          ) : (
            <div className="stack" style={{ "--gap": "10px" } as React.CSSProperties}>
              {top.map((i) => <IssueCard key={i.id} issue={i} compact />)}
              {issues.length > 3 && <Link to={`/d/${id}/clean`} className="link-btn">See all {issues.length} primary issues →</Link>}
            </div>
          )}
        </section>
      </div>

      <section aria-label="Data">
        <h2 style={{ marginBottom: 10 }}>Data</h2>
        <RowsTable />
      </section>
    </>
  );
}
