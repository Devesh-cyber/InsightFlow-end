import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRef, useState, type DragEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useToast } from "../components/Toast";
import { Empty, ErrorState, Note, PageHead, Skeleton } from "../components/ui";
import { useDatasets, useMe } from "../hooks/data";
import { api, uploadDataset } from "../lib/api";
import { bytes, num, shortDate } from "../lib/format";
import type { DatasetSummary } from "../lib/types";

const ACCEPT = ".csv,.tsv,.txt,.xlsx";

function UploadBox({ onDone }: { onDone: (id: string) => void }) {
  const me = useMe();
  const { fail } = useToast();
  const input = useRef<HTMLInputElement>(null);
  const [drag, setDrag] = useState(false);
  const [progress, setProgress] = useState<number | null>(null);
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const qc = useQueryClient();
  const csvMb = me.data?.usage.max_upload_mb ?? 25;
  const xlsxMb = me.data?.usage.max_xlsx_upload_mb ?? 10;

  async function start(file: File | undefined) {
    if (!file) return;
    setError(null);
    const lower = file.name.toLowerCase();
    if (lower.endsWith(".xls")) return setError("Old .xls files aren't supported. Open it in Excel or Sheets and save as .xlsx or .csv.");
    if (!/\.(csv|tsv|txt|xlsx)$/.test(lower)) return setError("Choose a .csv or .xlsx file.");
    const limit = lower.endsWith(".xlsx") ? xlsxMb : csvMb;
    if (file.size > limit * 1024 * 1024) return setError(`That file is ${bytes(file.size)}; the limit for this type is ${limit} MB.`);
    setName(file.name);
    setProgress(0);
    try {
      const ds = await uploadDataset(file, setProgress);
      qc.setQueryData(["dataset", ds.id], ds);
      qc.invalidateQueries({ queryKey: ["datasets"] });
      qc.invalidateQueries({ queryKey: ["me"] });
      onDone(ds.id);
    } catch (e) {
      fail(e);
      setError(e instanceof Error ? e.message : "Upload failed.");
    } finally {
      setProgress(null);
      if (input.current) input.current.value = "";
    }
  }

  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDrag(false);
    start(e.dataTransfer.files[0]);
  };

  return (
    <div className="stack">
      <label
        onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
        onDragLeave={() => setDrag(false)}
        onDrop={onDrop}
        style={{
          display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 8, padding: "40px 24px",
          border: `1.5px dashed var(--accent)`, borderRadius: 12, background: drag ? "var(--accent-soft)" : "var(--sunken)",
          textAlign: "center", cursor: progress === null ? "pointer" : "progress",
        }}
      >
        <input ref={input} type="file" accept={ACCEPT} className="sr-only" disabled={progress !== null} onChange={(e) => start(e.target.files?.[0])} />
        {progress === null ? (
          <>
            <span style={{ fontWeight: 600, fontSize: 15, color: "var(--strong)" }}>
              Drop a file here, or <span style={{ color: "var(--accent)", textDecoration: "underline" }}>browse</span>
            </span>
            <span className="small muted">.csv up to {csvMb} MB · .xlsx up to {xlsxMb} MB</span>
          </>
        ) : (
          <div className="stack" style={{ width: "min(360px, 100%)" }}>
            <span className="strong">{progress < 1 ? `Uploading ${name}…` : `Reading ${name}…`}</span>
            <div className="progress" aria-label="Upload progress"><span style={{ width: `${Math.round(progress * 100)}%` }} /></div>
            <span className="xs muted">{progress < 1 ? `${Math.round(progress * 100)}%` : "Detecting types and checking quality. Large files take a few seconds."}</span>
          </div>
        )}
      </label>
      {error && <Note tone="danger">{error}</Note>}
    </div>
  );
}

function SheetPicker({ ds }: { ds: DatasetSummary }) {
  const qc = useQueryClient();
  const { fail, notify } = useToast();
  const switchSheet = useMutation({
    mutationFn: (sheet: string) => api.selectSheet(ds.id, sheet, ds.current_version),
    onSuccess: (detail) => {
      qc.setQueryData(["dataset", ds.id], detail);
      qc.invalidateQueries({ queryKey: ["datasets"] });
      notify(`Loaded sheet '${detail.sheet_name}' as version ${detail.current_version}.`);
    },
    onError: fail,
  });
  if (!ds.sheet_names || ds.sheet_names.length < 2) return null;
  return (
    <section className="card stack" aria-label="Choose a sheet">
      <div className="spread" style={{ alignItems: "baseline" }}>
        <h2>{ds.source_filename} has {ds.sheet_names.length} sheets</h2>
        <span className="xs muted">The first sheet is loaded by default</span>
      </div>
      <div role="radiogroup" className="stack" style={{ "--gap": "8px" } as React.CSSProperties}>
        {ds.sheet_names.map((s) => {
          const active = s === ds.sheet_name;
          return (
            <label key={s} className="row" style={{
              gap: 12, padding: "12px 14px", borderRadius: 10, cursor: active ? "default" : "pointer",
              border: active ? "1.5px solid var(--accent)" : "1px solid var(--border)", background: active ? "var(--accent-soft)" : "transparent",
            }}>
              <input type="radio" name={`sheet-${ds.id}`} checked={active} disabled={switchSheet.isPending}
                onChange={() => switchSheet.mutate(s)} style={{ accentColor: "var(--accent)" }} />
              <span className="grow" style={{ fontWeight: active ? 600 : 400, color: active ? "var(--accent-strong)" : undefined }}>{s}</span>
              {active && <span className="mono xs" style={{ color: "var(--accent-strong)" }}>loaded</span>}
              {switchSheet.isPending && switchSheet.variables === s && <span className="spinner" />}
            </label>
          );
        })}
      </div>
      <p className="xs muted">Switching sheets adds a new version, so you can always go back from History.</p>
    </section>
  );
}

function DatasetRow({ ds }: { ds: DatasetSummary }) {
  const qc = useQueryClient();
  const { fail, notify } = useToast();
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(ds.name);
  const [confirm, setConfirm] = useState(false);

  const rename = useMutation({
    mutationFn: () => api.rename(ds.id, name.trim()),
    onSuccess: (d) => { qc.setQueryData(["dataset", ds.id], d); qc.invalidateQueries({ queryKey: ["datasets"] }); setEditing(false); },
    onError: fail,
  });
  const remove = useMutation({
    mutationFn: () => api.remove(ds.id),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["datasets"] }); qc.invalidateQueries({ queryKey: ["me"] }); notify(`Deleted ${ds.name}.`); },
    onError: fail,
  });

  return (
    <div className="row" style={{ padding: "14px 16px", borderTop: "1px solid var(--line)", gap: 16 }}>
      <div className="grow stack" style={{ "--gap": "2px" } as React.CSSProperties}>
        {editing ? (
          <form className="row" style={{ gap: 8 }} onSubmit={(e) => { e.preventDefault(); if (name.trim()) rename.mutate(); }}>
            <input className="input" style={{ height: 34 }} value={name} maxLength={200} autoFocus onChange={(e) => setName(e.target.value)} aria-label="New name" />
            <button className="btn sm primary" disabled={!name.trim() || rename.isPending}>Save</button>
            <button type="button" className="btn sm ghost" onClick={() => { setEditing(false); setName(ds.name); }}>Cancel</button>
          </form>
        ) : (
          <Link to={`/d/${ds.id}/overview`} className="strong" style={{ color: "var(--strong)", textDecoration: "none" }}>
            {ds.name}{ds.sheet_name && <span className="muted" style={{ fontWeight: 400 }}> · {ds.sheet_name}</span>}
          </Link>
        )}
        <span className="mono xs muted">
          {num(ds.row_count)} × {num(ds.column_count)} · v{ds.current_version} · {ds.source_format.toUpperCase()} {bytes(ds.source_size_bytes)} · updated {shortDate(ds.updated_at)}
        </span>
      </div>
      {!editing && (
        confirm ? (
          <div className="row" style={{ gap: 8 }}>
            <span className="small">Delete for good?</span>
            <button className="btn sm danger" onClick={() => remove.mutate()} disabled={remove.isPending}>Delete</button>
            <button className="btn sm ghost" onClick={() => setConfirm(false)}>Keep</button>
          </div>
        ) : (
          <div className="row" style={{ gap: 4 }}>
            <Link className="btn sm soft" to={`/d/${ds.id}/overview`}>Open</Link>
            <button className="btn sm ghost" onClick={() => setEditing(true)}>Rename</button>
            <button className="btn sm ghost danger" onClick={() => setConfirm(true)}>Delete</button>
          </div>
        )
      )}
    </div>
  );
}

export default function Datasets() {
  const list = useDatasets();
  const navigate = useNavigate();
  const xlsx = list.data?.datasets.filter((d) => (d.sheet_names?.length ?? 0) > 1) ?? [];

  return (
    <>
      <PageHead title="Datasets" sub="Upload a CSV or Excel file. Your original is kept as version 1 and never changed." />
      <div className="row" style={{ alignItems: "flex-start", gap: 24 }}>
        <div className="stack" style={{ flex: "3 1 420px", minWidth: 0, "--gap": "16px" } as React.CSSProperties}>
          <UploadBox onDone={(id) => navigate(`/d/${id}/overview`)} />
          {xlsx.slice(0, 1).map((d) => <SheetPicker key={d.id} ds={d} />)}
        </div>
        <section aria-label="Your datasets" style={{ flex: "2 1 320px", minWidth: 0 }}>
          <div className="spread" style={{ marginBottom: 10, alignItems: "baseline" }}>
            <h2>Your datasets</h2>
            {list.data && <span className="xs muted">{list.data.datasets.length} of {list.data.limit}</span>}
          </div>
          {list.isPending ? <Skeleton height={140} /> : list.isError ? <ErrorState error={list.error} retry={() => list.refetch()} /> : (
            list.data.datasets.length === 0 ? (
              <div className="card"><Empty title="No datasets yet">Upload a file to see its overview, health and columns.</Empty></div>
            ) : (
              <div style={{ border: "1px solid var(--border)", borderRadius: 12, overflow: "hidden" }}>
                <div style={{ marginTop: -1 }}>{list.data.datasets.map((d) => <DatasetRow key={d.id} ds={d} />)}</div>
              </div>
            )
          )}
        </section>
      </div>
      {xlsx.length > 1 && <p className="xs muted">Other Excel files: switch their sheet from inside the dataset (Overview).</p>}
    </>
  );
}
