import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { useToast } from "../components/Toast";
import { ErrorState, Loading, Note, PageHead } from "../components/ui";
import { useAfterChange, useDataset, useDatasetId, useVersions } from "../hooks/data";
import { api } from "../lib/api";
import { num, shortDate } from "../lib/format";

export default function History() {
  const id = useDatasetId();
  const ds = useDataset(id);
  const versions = useVersions();
  const after = useAfterChange();
  const { notify, fail } = useToast();
  const [busy, setBusy] = useState<number | null>(null);
  const [recipeBusy, setRecipeBusy] = useState(false);

  const revert = useMutation({
    mutationFn: (to: number) => api.revert(id, to, ds.data!.current_version),
    onMutate: (to) => setBusy(to),
    onSuccess: (d, to) => { after(d); notify(`Reverted to version ${to}. Saved as version ${d.current_version}.`); },
    onError: fail,
    onSettled: () => setBusy(null),
  });

  if (versions.isPending || ds.isPending) return <Loading />;
  if (versions.isError) return <ErrorState error={versions.error} retry={() => versions.refetch()} />;
  const list = [...versions.data.versions].sort((a, b) => b.version - a.version);
  const oldest = list[list.length - 1]?.version ?? 1;

  return (
    <>
      <PageHead title="History" sub="Every change is a new version. The original file is never touched."
        actions={
          <button className="btn" disabled={recipeBusy} onClick={async () => {
            setRecipeBusy(true);
            try { await api.recipe(id); } catch (e) { fail(e); } finally { setRecipeBusy(false); }
          }}>{recipeBusy && <span className="spinner" />}Download pandas recipe</button>
        } />

      {oldest > 1 && (
        <Note tone="neutral">Only the latest {versions.data.retention} versions are kept, so versions before v{oldest} were removed.</Note>
      )}

      <ol className="stack" style={{ listStyle: "none", margin: 0, padding: 0 }}>
        {list.map((v) => (
          <li key={v.version} className={`card ${v.is_current ? "accent" : ""}`} style={{ display: "flex", gap: 16, alignItems: "flex-start" }}>
            <span className="mono small" style={{
              flex: "none", padding: "3px 9px", borderRadius: 6, fontWeight: 500,
              background: v.is_current ? "var(--primary)" : "var(--chip)", color: v.is_current ? "var(--on-primary)" : "var(--chip-text)",
            }}>v{v.version}</span>
            <div className="grow stack" style={{ "--gap": "6px" } as React.CSSProperties}>
              <div className="row" style={{ gap: 10 }}>
                <span className="strong">{v.description}</span>
                {v.is_current && <span className="badge accent">Current</span>}
              </div>
              <span className="mono xs muted">
                {v.cells_changed !== null ? `${num(v.cells_changed)} cell${v.cells_changed === 1 ? "" : "s"} changed · ` : ""}
                {v.rows_removed ? `${num(v.rows_removed)} rows removed · ` : ""}
                {num(v.rows)} rows × {num(v.columns)} columns · {shortDate(v.created_at)}
              </span>
              {v.warnings.map((w) => (
                <span key={w} className="small" style={{ alignSelf: "flex-start", color: "var(--warn)", background: "var(--warn-soft)", padding: "6px 10px", borderRadius: 8 }}>{w}</span>
              ))}
            </div>
            {!v.is_current && (
              <button className="btn sm" disabled={revert.isPending} onClick={() => revert.mutate(v.version)}>
                {busy === v.version && <span className="spinner" />}Revert to v{v.version}
              </button>
            )}
          </li>
        ))}
      </ol>
      <p className="small muted">Reverting doesn't delete anything: it adds a new version that matches the one you picked.</p>
    </>
  );
}
