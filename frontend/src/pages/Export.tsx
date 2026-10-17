import { useState } from "react";
import { useAuth } from "../auth/AuthProvider";
import { useToast } from "../components/Toast";
import { ErrorState, Loading, Note, PageHead } from "../components/ui";
import { useDataset, useDatasetId } from "../hooks/data";
import { api, saveBlob } from "../lib/api";
import { num } from "../lib/format";

function useAction() {
  const { fail } = useToast();
  const [busy, setBusy] = useState<string | null>(null);
  const run = async (key: string, fn: () => Promise<unknown>) => {
    setBusy(key);
    try {
      await fn();
    } catch (e) {
      fail(e);
    } finally {
      setBusy(null);
    }
  };
  return { busy, run };
}

export default function Export() {
  const id = useDatasetId();
  const ds = useDataset(id);
  const { session } = useAuth();
  const { busy, run } = useAction();
  const [title, setTitle] = useState("");
  const [author, setAuthor] = useState(() => (session?.user.user_metadata?.full_name as string | undefined) ?? "");
  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;

  if (ds.isPending) return <Loading />;
  if (ds.isError) return <ErrorState error={ds.error} retry={() => ds.refetch()} />;
  const d = ds.data;
  const cells = d.row_count * d.column_count;
  const reportQuery = { title: title.trim() || undefined, author: author.trim() || undefined, tz };
  const Spin = ({ k }: { k: string }) => (busy === k ? <span className="spinner" /> : null);

  async function openReport() {
    // Open the tab first (browsers block pop-ups opened after an await), then fill it.
    const tab = window.open("", "_blank");
    let blob: Blob;
    try {
      blob = await api.reportHtml(id, reportQuery);
    } catch (e) {
      tab?.close();
      throw e;
    }
    const href = URL.createObjectURL(blob);
    if (tab) tab.location.href = href;
    else saveBlob(blob, `${d.name}_report.html`);
    setTimeout(() => URL.revokeObjectURL(href), 60_000);
  }

  return (
    <>
      <PageHead title="Export & report" sub={`${d.name} · version ${d.current_version} · ${num(d.row_count)} rows × ${num(d.column_count)} columns`} />

      <div className="row" style={{ alignItems: "stretch", gap: 24 }}>
        <section className="card stack" style={{ flex: "1 1 320px", "--gap": "14px" } as React.CSSProperties} aria-label="Export data">
          <div>
            <h2>Cleaned data</h2>
            <p className="small muted" style={{ marginTop: 4 }}>Download the current version. Leading zeros and dates are kept.</p>
          </div>
          <div className="row" style={{ gap: 8 }}>
            <button className="btn primary" disabled={!!busy} onClick={() => run("csv", () => api.exportFile(id, "csv"))}><Spin k="csv" />Download CSV</button>
            <button className="btn" disabled={!!busy} onClick={() => run("xlsx", () => api.exportFile(id, "xlsx"))}><Spin k="xlsx" />Download Excel</button>
          </div>
          {cells > 1_000_000 && <p className="xs muted">Large file: Excel export may be refused on the free server; CSV always works.</p>}
          <div style={{ borderTop: "1px solid var(--line)", paddingTop: 14 }} className="stack">
            <h3>Reproduce it in Python</h3>
            <p className="small muted">A pandas script that repeats every cleaning step on the original file.</p>
            <div><button className="btn" disabled={!!busy} onClick={() => run("recipe", () => api.recipe(id))}><Spin k="recipe" />Download recipe.py</button></div>
          </div>
        </section>

        <section className="card stack" style={{ flex: "1.4 1 380px", "--gap": "14px" } as React.CSSProperties} aria-label="Report">
          <div>
            <h2>Report for coursework or a presentation</h2>
            <p className="small muted" style={{ marginTop: 4 }}>
              Summary, data dictionary, quality before and after, cleaning log, column profiles with charts, relationships, the pandas recipe and methods.
            </p>
          </div>
          <label className="field">Title
            <input className="input" maxLength={120} value={title} placeholder={`Data report: ${d.name}`} onChange={(e) => setTitle(e.target.value)} />
          </label>
          <label className="field">Prepared by
            <input className="input" maxLength={80} value={author} placeholder="Your name" onChange={(e) => setAuthor(e.target.value)} />
          </label>
          <div className="row" style={{ gap: 8 }}>
            <button className="btn primary" disabled={!!busy} onClick={() => run("open", openReport)}><Spin k="open" />Open report (save as PDF)</button>
            <button className="btn" disabled={!!busy} onClick={() => run("html", () => api.report(id, { ...reportQuery, format: "html" }))}><Spin k="html" />Download HTML</button>
            <button className="btn" disabled={!!busy} onClick={() => run("docx", () => api.report(id, { ...reportQuery, format: "docx" }))}><Spin k="docx" />Download Word</button>
          </div>
          <Note tone="neutral">
            To get a PDF, open the report and press Ctrl + P (⌘ + P on Mac), then choose "Save as PDF". The Word file is editable but has no charts.
          </Note>
        </section>
      </div>
    </>
  );
}
