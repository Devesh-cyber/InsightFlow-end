import { useState } from "react";
import { IssueCard } from "../components/IssueCard";
import { ErrorState, Loading, Note, PageHead } from "../components/ui";
import { useHealth } from "../hooks/data";
import { gradeLabel, num } from "../lib/format";
import type { Severity } from "../lib/types";

const CATEGORY: Record<string, string> = {
  missing_values: "Missing values", type_mismatch: "Type fixes", duplicates: "Duplicates", outliers: "Outliers",
  whitespace: "Whitespace", empty_column: "Empty columns", constant_column: "Constant columns",
};

export default function Health() {
  const health = useHealth();
  const [filter, setFilter] = useState<Severity | "all">("all");
  if (health.isPending) return <Loading />;
  if (health.isError) return <ErrorState error={health.error} retry={() => health.refetch()} />;
  const h = health.data;
  const lost = Math.round((100 - h.score) * 10) / 10;
  const shown = filter === "all" ? h.issues : h.issues.filter((i) => i.severity === filter);

  return (
    <>
      <PageHead title="Health" sub="What's wrong with the data, and what each fix would change. Nothing is applied until you preview it." />

      <section aria-label="Score" className="row" style={{ alignItems: "stretch", gap: 12 }}>
        <div className={`tile ${h.score >= 80 ? "accent" : "warn"}`} style={{ flex: "1 1 220px", display: "flex", alignItems: "baseline", gap: 12 }}>
          <span className="t-value" style={{ fontSize: 40, lineHeight: 1 }}>{num(h.score)}</span>
          <span className="t-label" style={{ fontSize: 14, fontWeight: 600 }}>{gradeLabel[h.grade]}</span>
        </div>
        <div className="tile" style={{ flex: "2 1 340px" }}>
          <div className="t-label" style={{ marginBottom: 8 }}>{lost > 0 ? `Where the ${num(lost)} points went` : "No points lost"}</div>
          <div className="stack" style={{ "--gap": "6px" } as React.CSSProperties}>
            {h.deductions.length === 0 && <span className="small muted">Nothing deducted.</span>}
            {h.deductions.map((d) => (
              <div key={d.reason} className="spread" style={{ alignItems: "baseline" }}><span>{d.reason}</span><span className="mono">−{num(d.points)}</span></div>
            ))}
          </div>
        </div>
        <div className="tile" style={{ flex: "1 1 200px" }}>
          <div className="t-label" style={{ marginBottom: 8 }}>Issues</div>
          <div className="stack" style={{ "--gap": "4px" } as React.CSSProperties}>
            <div className="spread"><span>Critical</span><span className="mono">{h.summary.critical}</span></div>
            <div className={`spread ${h.summary.warning ? "warn-text" : ""}`}><span>Warnings</span><span className="mono">{h.summary.warning}</span></div>
            <div className="spread"><span>Info</span><span className="mono">{h.summary.info}</span></div>
          </div>
        </div>
      </section>

      {h.issues.length === 0 ? (
        <Note tone="accent" title="No issues found.">Every check passed for this version.</Note>
      ) : (
        <section aria-label="Issues" className="stack">
          <div className="spread" style={{ alignItems: "center" }}>
            <h2>{shown.length} issue{shown.length === 1 ? "" : "s"}</h2>
            <div className="segmented" role="tablist" aria-label="Filter by severity">
              {(["all", "critical", "warning", "info"] as const).map((s) => (
                <button key={s} role="tab" aria-selected={filter === s} onClick={() => setFilter(s)}>
                  {s === "all" ? "All" : s[0].toUpperCase() + s.slice(1)}
                </button>
              ))}
            </div>
          </div>
          {shown.map((i) => <IssueCard key={i.id} issue={i} />)}
          {shown.length === 0 && <p className="muted small">No {filter} issues.</p>}
          <p className="xs muted">
            {Object.entries(h.summary.by_category).map(([k, v]) => `${CATEGORY[k] ?? k}: ${v}`).join(" · ")}
          </p>
        </section>
      )}
      {h.notes.map((n) => <p key={n} className="xs muted">{n}</p>)}
    </>
  );
}
