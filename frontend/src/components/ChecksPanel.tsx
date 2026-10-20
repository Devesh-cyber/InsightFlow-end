import { useChecks, useSaveChecks } from "../hooks/data";
import { cell, num } from "../lib/format";
import type { CheckDef } from "../lib/types";
import { errorText, useToast } from "./Toast";
import { ShowRows } from "./MatchingRowsTable";
import { ErrorState, Note, Skeleton } from "./ui";

const STATUS: Record<string, { text: string; color: string }> = {
  passing: { text: "passing", color: "var(--accent-strong)" },
  failing: { text: "failing", color: "var(--danger)" },
  error: { text: "can't run", color: "var(--warn)" },
};

/** Saved checks with their result on the current version. */
export function ChecksPanel() {
  const checks = useChecks();
  const save = useSaveChecks();
  const { notify } = useToast();

  if (checks.isPending) return <Skeleton height={80} />;
  if (checks.isError) return <ErrorState error={checks.error} retry={() => checks.refetch()} />;
  const list = checks.data.checks;

  const remove = (id: string) => {
    const rest: CheckDef[] = list.filter((c) => c.id !== id).map(({ result: _r, ...c }) => c);
    save.mutate(rest, { onSuccess: () => notify("Check deleted."), onError: (e) => notify(errorText(e)) });
  };

  return (
    <div className="stack" style={{ "--gap": "8px" } as React.CSSProperties}>
      {list.length === 0 ? (
        <p className="small muted">No saved checks yet. Build conditions above and choose "Save check" to keep a rule that's re-checked on every version.</p>
      ) : list.map((c) => {
        const r = c.result;
        const s = STATUS[r?.status ?? "error"];
        return (
          <div key={c.id} className="card stack" style={{ "--gap": "4px", padding: "12px 14px" } as React.CSSProperties}>
            <div className="row" style={{ gap: 8 }}>
              <span className="strong" style={{ flex: 1, minWidth: 0 }}>{c.name}</span>
              <span className="chip" style={{ color: s.color }}>{s.text}{r?.status === "failing" && ` · ${num(r.violations ?? 0)} row(s)`}</span>
              <button className="btn ghost" aria-label={`Delete check ${c.name}`} disabled={save.isPending} onClick={() => remove(c.id)}>✕</button>
            </div>
            {r && <div className="xs muted">Problem when: {r.rule}</div>}
            {r?.status === "error" && <div className="xs warn-text">{r.error}</div>}
            {r?.status === "failing" && r.rows.length > 0 && (
              <div className="xs muted">
                e.g. {r.rows.map((x) => `row ${x.row} (${x.values.map((v) => (v === null ? "missing" : cell(v))).join(", ")})`).join(" · ")}
                {(r.violations ?? 0) > r.rows.length && " …"}
              </div>
            )}
            {r?.status === "failing" && <ShowRows match={{ conditions: c.conditions, combine: c.combine }} label="Show failing rows" />}
          </div>
        );
      })}
      {list.some((c) => c.result?.status === "failing") && (
        <Note tone="neutral">Failing checks are listed under "Issues to review" with fixes you can preview.</Note>
      )}
    </div>
  );
}
