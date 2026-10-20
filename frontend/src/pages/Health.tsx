import { useState } from "react";
import { StageIssues, StageStrip, defaultStage, type StageTab } from "../components/Stages";
import { ErrorState, Loading, PageHead } from "../components/ui";
import { useHealth } from "../hooks/data";
import { gradeLabel, num } from "../lib/format";

const CATEGORY: Record<string, string> = {
  missing_values: "Missing values", type_mismatch: "Type fixes", duplicates: "Duplicates", outliers: "Outliers",
  whitespace: "Whitespace", empty_column: "Empty columns", constant_column: "Constant columns", skewed: "Skewed",
  duplicate_id: "Repeated IDs", invalid_values: "Values that look wrong", broken_relationship: "Columns that don't add up",
  inconsistent_spelling: "Spellings", check_failed: "Your checks", check_error: "Checks that can't run",
};

export default function Health() {
  const health = useHealth();
  const [stage, setStage] = useState<StageTab | null>(null);
  if (health.isPending) return <Loading />;
  if (health.isError) return <ErrorState error={health.error} retry={() => health.refetch()} />;
  const h = health.data;
  const lost = Math.round((100 - h.score) * 10) / 10;
  const active = stage ?? defaultStage(h);

  return (
    <>
      <PageHead title="Health" sub="Clean in order: primary fixes first, then review outliers and skew while exploring. Nothing changes until you preview and apply." />

      <section aria-label="Score" className="row" style={{ alignItems: "stretch", gap: 12 }}>
        <div className={`tile ${h.score >= 80 ? "accent" : "warn"}`} style={{ flex: "1 1 220px", display: "flex", alignItems: "baseline", gap: 12 }}>
          <span className="t-value" style={{ fontSize: 40, lineHeight: 1 }}>{num(h.score)}</span>
          <span className="stack" style={{ "--gap": "2px" } as React.CSSProperties}>
            <span className="t-label" style={{ fontSize: 14, fontWeight: 600 }}>{gradeLabel[h.grade]}</span>
            <span className="xs muted">primary cleaning score</span>
          </span>
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
          <div className="t-label" style={{ marginBottom: 8 }}>Open</div>
          <div className="stack" style={{ "--gap": "4px" } as React.CSSProperties}>
            <div className={`spread ${h.stages?.primary.open ? "warn-text" : ""}`}><span>Primary cleaning</span><span className="mono">{h.stages?.primary.open ?? 0}</span></div>
            <div className="spread"><span>Explore & review</span><span className="mono">{h.stages?.secondary.open ?? 0}</span></div>
            <div className="spread muted"><span>Kept as they are</span><span className="mono">{(h.stages?.primary.reviewed ?? 0) + (h.stages?.secondary.reviewed ?? 0)}</span></div>
          </div>
        </div>
      </section>

      <section aria-label="Issues" className="stack">
        <StageStrip health={h} active={active} onSelect={setStage} />
        <StageIssues health={h} stage={active} onStage={setStage} />
        <p className="xs muted">
          {Object.entries(h.summary.by_category).map(([k, v]) => `${CATEGORY[k] ?? k}: ${v}`).join(" · ")}
        </p>
      </section>
      {h.notes.map((n) => <p key={n} className="xs muted">{n}</p>)}
    </>
  );
}
