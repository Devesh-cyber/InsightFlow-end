import { useNavigate } from "react-router-dom";
import { useDatasetId } from "../hooks/data";
import type { Issue, Operation } from "../lib/types";
import { Sev } from "./ui";

export function usePreviewOperation() {
  const id = useDatasetId();
  const navigate = useNavigate();
  return (operation: Operation, label?: string) => navigate(`/d/${id}/clean`, { state: { operation, label } });
}

/** One data-quality issue with its fix options. Nothing changes until the user previews and applies. */
export function IssueCard({ issue, compact = false }: { issue: Issue; compact?: boolean }) {
  const preview = usePreviewOperation();
  const rec = issue.options.find((o) => o.recommended) ?? issue.options[0];

  if (compact) {
    return (
      <div className="card stack" style={{ "--gap": "4px", padding: "14px 16px" } as React.CSSProperties}>
        <div className="strong">{issue.title.replace(/\.$/, "")}</div>
        <div className="small muted">{issue.message}</div>
        {rec && (
          <button className="link-btn" style={{ alignSelf: "flex-start", marginTop: 4 }} onClick={() => preview(rec.operation, rec.label)}>
            Preview: {rec.label.toLowerCase()} →
          </button>
        )}
      </div>
    );
  }

  return (
    <div className="issue">
      <div className="issue-head">
        <Sev severity={issue.severity} />
        <span className="strong">{issue.title.replace(/\.$/, "")}</span>
        {issue.column && <span className="chip">{issue.column}</span>}
      </div>
      <p className="small muted">{issue.message}</p>
      {issue.options.length > 0 && (
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
    </div>
  );
}
