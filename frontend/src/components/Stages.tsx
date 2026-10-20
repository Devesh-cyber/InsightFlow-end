import { useState } from "react";
import { Link } from "react-router-dom";
import { useDatasetId } from "../hooks/data";
import type { Health, Issue, Stage } from "../lib/types";
import { IssueCard } from "./IssueCard";
import { Note } from "./ui";

export type StageTab = Stage | "ml";

export const STAGE_INFO: Record<StageTab, { n: number; title: string; short: string }> = {
  primary: { n: 1, title: "Primary cleaning", short: "Fix wrong types, spellings, duplicates, invalid values and gaps." },
  secondary: { n: 2, title: "Explore & review", short: "Look at outliers and skew while exploring. Often real: check before changing." },
  ml: { n: 3, title: "ML-ready", short: "Your rules per column → a dataset ready for model training." },
};

/** The stage to open by default: primary until it's done, then secondary. */
export function defaultStage(h: Health | undefined): StageTab {
  if (!h?.stages) return "primary";
  if (h.stages.primary.open > 0) return "primary";
  if (h.stages.secondary.open > 0) return "secondary";
  return "ml";
}

export const stageOf = (i: Issue): Stage => i.stage ?? "primary";

/** 1 Primary → 2 Explore & review → 3 ML-ready. Clickable when onSelect is given. */
export function StageStrip({ health, active, onSelect }: { health: Health; active?: StageTab; onSelect?: (s: StageTab) => void }) {
  const st = health.stages;
  const primaryDone = !!st && st.primary.open === 0;
  const secondaryDone = !!st && st.secondary.open === 0;
  const status: Record<StageTab, string> = {
    primary: !st ? "" : primaryDone ? "done ✓" : `${st.primary.open} to fix`,
    secondary: !st ? "" : secondaryDone ? (primaryDone ? "done ✓" : "nothing yet") : `${st.secondary.open} to look at`,
    ml: primaryDone && secondaryDone ? "unlocked" : "locked until cleaned",
  };
  return (
    <nav className="stages" aria-label="Cleaning stages">
      {(["primary", "secondary", "ml"] as StageTab[]).map((s, i) => {
        const done = (s === "primary" && primaryDone) || (s === "secondary" && primaryDone && secondaryDone);
        const content = (
          <>
            <span className={`stage-n ${done ? "done" : ""}`}>{done ? "✓" : STAGE_INFO[s].n}</span>
            <span className="stage-text">
              <span className="strong">{STAGE_INFO[s].title}</span>
              <span className="xs muted">{status[s]}</span>
            </span>
          </>
        );
        return (
          <span key={s} className="stage-wrap">
            {i > 0 && <span className="stage-arrow" aria-hidden="true">→</span>}
            {onSelect ? (
              <button type="button" className="stage" aria-pressed={active === s} onClick={() => onSelect(s)}>{content}</button>
            ) : <span className="stage">{content}</span>}
          </span>
        );
      })}
    </nav>
  );
}

/** Issues for one stage, with the "done" banner, a soft gate for secondary, and the reviewed list. */
export function StageIssues({ health, stage, onStage }: { health: Health; stage: StageTab; onStage: (s: StageTab) => void }) {
  const id = useDatasetId();
  const [showEarly, setShowEarly] = useState(false);
  const st = health.stages;
  const primaryOpen = st?.primary.open ?? 0;

  if (stage === "ml") {
    return (
      <div className="card stack" style={{ "--gap": "10px" } as React.CSSProperties}>
        <div className="strong">Make an ML-ready dataset</div>
        <p className="small muted">
          Decide, column by column, what goes into a model: keep or drop it, fill gaps, clip, transform (log), scale, encode
          categories, split dates, and create new features. Then download one CSV, or train and test files.
        </p>
        <ul className="small" style={{ margin: 0, paddingLeft: 18 }}>
          <li>Primary cleaning: {primaryOpen === 0 ? "done ✓" : `${primaryOpen} issue(s) left`} {primaryOpen > 0 && <button className="link-btn" onClick={() => onStage("primary")}>go →</button>}</li>
          <li>Explore & review: {st?.secondary.open ? `${st.secondary.open} item(s) to look at` : "done ✓"} {!!st?.secondary.open && <button className="link-btn" onClick={() => onStage("secondary")}>go →</button>}</li>
        </ul>
        {primaryOpen === 0 && !st?.secondary.open
          ? <div><Link className="btn primary" to={`/d/${id}/ml`}>Open ML-ready →</Link></div>
          : <p className="small" style={{ margin: 0 }}>🔒 Unlocks when both are done. <Link to={`/d/${id}/ml`}>Look at the ML-ready page</Link> (read-only for now).</p>}
      </div>
    );
  }

  const inStage = health.issues.filter((i) => stageOf(i) === stage);
  const open = inStage.filter((i) => !i.reviewed);
  const kept = inStage.filter((i) => i.reviewed);
  const gated = stage === "secondary" && primaryOpen > 0 && !showEarly;

  return (
    <div className="stack">
      <p className="small muted" style={{ margin: 0 }}>{STAGE_INFO[stage].short}</p>

      {stage === "primary" && open.length === 0 && (
        <Note tone="accent" title="Primary cleaning done ✓">
          Next, explore your data (<Link to={`/d/${id}/columns`}>Columns</Link>, <Link to={`/d/${id}/charts`}>Charts</Link>,{" "}
          <Link to={`/d/${id}/relationships`}>Relationships</Link>) and look at the{" "}
          <button className="link-btn" onClick={() => onStage("secondary")}>{st?.secondary.open ?? 0} secondary item(s)</button>.
        </Note>
      )}

      {stage === "secondary" && gated ? (
        <Note tone="neutral" title={`Finish primary cleaning first (${primaryOpen} left)`}>
          Outliers and skew are easier to judge once types, spellings and duplicates are fixed, and fixing primary issues often
          changes them. <button className="link-btn" onClick={() => onStage("primary")}>Go to primary cleaning</button>
          {open.length > 0 && <> · <button className="link-btn" onClick={() => setShowEarly(true)}>show the {open.length} item(s) anyway</button></>}
        </Note>
      ) : stage === "secondary" && open.length === 0 ? (
        <Note tone="accent" title="Nothing to review">
          No outliers or skew to look at. {primaryOpen === 0 && <button className="link-btn" onClick={() => onStage("ml")}>Next: ML-ready →</button>}
        </Note>
      ) : (
        open.map((i) => <IssueCard key={i.id} issue={i} />)
      )}

      {kept.length > 0 && (
        <details className="kept">
          <summary className="small muted">Reviewed and kept as they are ({kept.length})</summary>
          <div className="stack" style={{ "--gap": "8px", marginTop: 8 } as React.CSSProperties}>
            {kept.map((i) => <IssueCard key={i.id} issue={i} />)}
          </div>
        </details>
      )}
    </div>
  );
}
