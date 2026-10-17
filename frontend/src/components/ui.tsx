import type { ReactNode } from "react";
import { errorText } from "./Toast";
import type { Severity } from "../lib/types";

export function Logo({ size = 22 }: { size?: number }) {
  return (
    <svg className="brand-mark" width={size} height={size} viewBox="0 0 32 32" aria-hidden="true">
      <rect width="32" height="32" rx="8" fill="var(--primary)" />
      <rect x="8" y="17" width="4" height="7" rx="1.5" fill="var(--on-primary)" />
      <rect x="14" y="11" width="4" height="13" rx="1.5" fill="var(--on-primary)" />
      <rect x="20" y="7" width="4" height="17" rx="1.5" fill="var(--on-primary)" />
    </svg>
  );
}

export function PageHead({ title, sub, actions, eyebrow }: { title: ReactNode; sub?: ReactNode; actions?: ReactNode; eyebrow?: ReactNode }) {
  return (
    <div className="page-head">
      <div>
        {eyebrow}
        <h1>{title}</h1>
        {sub && <p>{sub}</p>}
      </div>
      {actions && <div className="row">{actions}</div>}
    </div>
  );
}

export function Tile({ label, value, sub, tone, small }: { label: ReactNode; value: ReactNode; sub?: ReactNode; tone?: "accent" | "warn"; small?: boolean }) {
  return (
    <div className={`tile ${tone ?? ""} ${small ? "sm" : ""}`}>
      <div className="t-label">{label}</div>
      <div className="t-value">{value}</div>
      {sub && <div className="t-sub">{sub}</div>}
    </div>
  );
}

export function Note({ children, tone = "warn", title }: { children: ReactNode; tone?: "warn" | "neutral" | "danger" | "accent"; title?: ReactNode }) {
  return (
    <div className={`note ${tone === "warn" ? "" : tone}`} role={tone === "danger" ? "alert" : "note"}>
      <span className="note-icon" aria-hidden="true">{tone === "accent" ? "✓" : tone === "neutral" ? "i" : "!"}</span>
      <span>
        {title && <strong style={{ fontWeight: 600 }}>{title} </strong>}
        {children}
      </span>
    </div>
  );
}

export function Sev({ severity }: { severity: Severity }) {
  return <span className={`sev ${severity}`}>{severity}</span>;
}

export function Skeleton({ height = 120 }: { height?: number }) {
  return <div className="skeleton" style={{ height }} aria-hidden="true" />;
}

export function Loading({ rows = 3 }: { rows?: number }) {
  return (
    <div className="stack" aria-busy="true" aria-label="Loading">
      <Skeleton height={88} />
      {Array.from({ length: rows - 1 }, (_, i) => <Skeleton key={i} height={160} />)}
    </div>
  );
}

export function ErrorState({ error, retry }: { error: unknown; retry?: () => void }) {
  return (
    <div className="stack">
      <Note tone="danger" title="Couldn't load this.">{errorText(error)}</Note>
      {retry && <div><button className="btn sm" onClick={retry}>Try again</button></div>}
    </div>
  );
}

export function Empty({ title, children }: { title: ReactNode; children?: ReactNode }) {
  return (
    <div className="empty">
      <strong style={{ color: "var(--strong)", fontSize: 15 }}>{title}</strong>
      {children}
    </div>
  );
}

export function TypeChip({ dtype }: { dtype: string }) {
  const typed = dtype !== "text";
  return <span className={`chip ${typed ? "accent" : ""}`}>{dtype}</span>;
}
