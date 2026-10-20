import { useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { useDatasetId, useReview } from "../hooks/data";
import type { Issue, Operation } from "../lib/types";
import { ShowRows } from "./MatchingRowsTable";
import { errorText, useToast } from "./Toast";
import { Sev } from "./ui";

export function usePreviewOperation() {
  const id = useDatasetId();
  const navigate = useNavigate();
  const location = useLocation();
  // remember where the user came from, so Apply can bring them back there
  return (operation: Operation, label?: string) => navigate(`/d/${id}/clean`, { state: { operation, label, from: location.pathname } });
}

/** One data-quality issue with its fix options. Nothing changes until the user previews and applies. */
export function IssueCard({ issue, compact = false }: { issue: Issue; compact?: boolean }) {
  const preview = usePreviewOperation();
  const id = useDatasetId();
  const rec = issue.options.find((o) => o.recommended); // never guess a fix the backend didn't recommend

  if (compact) {
    return (
      <div className="card stack" style={{ "--gap": "4px", padding: "14px 16px" } as React.CSSProperties}>
        <div className="strong">{issue.title.replace(/\.$/, "")}</div>
        <div className="small muted">{issue.message}</div>
        {rec ? (
          <button className="link-btn" style={{ alignSelf: "flex-start", marginTop: 4 }} onClick={() => preview(rec.operation, rec.label)}>
            Preview: {rec.label.toLowerCase()} →
          </button>
        ) : issue.options.length > 0 && (
          <Link className="link-btn" style={{ alignSelf: "flex-start", marginTop: 4 }} to={`/d/${id}/health`}>
            Review options →
          </Link>
        )}
      </div>
    );
  }

  return <FullIssue issue={issue} />;
}

function FullIssue({ issue }: { issue: Issue }) {
  const preview = usePreviewOperation();
  const review = useReview();
  const { notify } = useToast();
  const secondary = issue.stage === "secondary";
  const [showFixes, setShowFixes] = useState(!secondary);
  const keep = (yes: boolean) => review.mutate({ issue, keep: yes }, {
    onSuccess: () => notify(yes ? `Kept as is: ${issue.title.replace(/\.$/, "")}. The data wasn't changed.` : "Moved back to open issues."),
    onError: (e) => notify(errorText(e)),
  });

  if (issue.reviewed) {
    return (
      <div className="issue kept-issue">
        <div className="issue-head">
          <span className="chip" style={{ color: "var(--accent-strong)" }}>kept ✓</span>
          <span className="small">{issue.title.replace(/\.$/, "")}</span>
          <button className="link-btn" style={{ marginLeft: "auto" }} disabled={review.isPending} onClick={() => keep(false)}>Undo</button>
        </div>
      </div>
    );
  }

  return (
    <div className="issue">
      <div className="issue-head">
        {secondary ? <span className="sev info">note</span> : <Sev severity={issue.severity} />}
        <span className="strong">{issue.title.replace(/\.$/, "")}</span>
        {issue.column && <span className="chip">{issue.column}</span>}
      </div>
      {issue.changed_since_review && (
        <p className="xs warn-text" style={{ margin: 0 }}>You marked this as fine earlier, but it has changed since (different rows or counts).</p>
      )}
      <p className="small muted">{issue.message}</p>
      {issue.match && (
        <ShowRows match={issue.match}
          emptyText={issue.category === "type_mismatch"
            ? "Every value can be converted; this is about the column's type, not specific rows."
            : "No rows match any more in this version."} />
      )}
      {issue.options.length > 0 && showFixes && (
        <div className="options">
          {issue.options.map((o) => (
            <button key={o.label} className={`option ${o.recommended ? "rec" : ""}`} title={o.note ?? undefined}
              onClick={() => preview(o.operation, o.label)}>
              {o.label}{o.recommended && " · recommended"}
              {o.note && !o.recommended && <span className="muted" style={{ fontWeight: 400 }}> ({o.note.replace(/\.$/, "")})</span>}
            </button>
          ))}
        </div>
      )}
      <div className="row" style={{ gap: 14 }}>
        {issue.options.length > 0 && !showFixes && (
          <button className="link-btn" onClick={() => setShowFixes(true)}>I checked: these look wrong · show fixes</button>
        )}
        <button className="link-btn keep-btn" disabled={review.isPending} onClick={() => keep(true)}
          title="Close this issue without changing the data">
          {secondary ? "These are real, keep them" : "Looks fine, keep it"}
        </button>
      </div>
    </div>
  );
}
