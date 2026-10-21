import { useEffect, useState, type ReactNode } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import { useAuth } from "../auth/AuthProvider";
import { useDataset, useDatasetId, useMe } from "../hooks/data";
import { supabase } from "../lib/supabase";
import { Logo } from "./ui";

const SECTIONS: [string, string][] = [
  ["overview", "Overview"],
  ["health", "Health"],
  ["columns", "Columns"],
  ["relationships", "Relationships"],
  ["charts", "Charts"],
  ["clean", "Clean"],
  ["ml", "ML-ready"],
  ["history", "History"],
  ["export", "Export & report"],
];

function Sidebar({ open, children }: { open: boolean; children: ReactNode }) {
  const { session } = useAuth();
  const me = useMe();
  return (
    <nav className={`sidebar ${open ? "open" : ""}`} aria-label="Main">
      <Link to="/app" className="brand"><Logo />InsightFlow</Link>
      {children}
      <div className="sidebar-foot">
        {me.data && (
          <span>{me.data.usage.datasets} of {me.data.usage.max_datasets} datasets used</span>
        )}
        <span style={{ overflow: "hidden", textOverflow: "ellipsis" }} title={session?.user.email ?? ""}>
          {session?.user.email}
        </span>
        <button className="link-btn" style={{ alignSelf: "flex-start", color: "var(--muted)" }} onClick={() => supabase.auth.signOut()}>
          Sign out
        </button>
      </div>
    </nav>
  );
}

function MobileBar({ onMenu }: { onMenu: () => void }) {
  return (
    <div className="mobile-bar">
      <Link to="/app" className="brand" style={{ padding: 0 }}><Logo />InsightFlow</Link>
      <button className="btn sm" onClick={onMenu} aria-label="Open menu">Menu</button>
    </div>
  );
}

function useCloseOnNavigate(setOpen: (v: boolean) => void) {
  const loc = useLocation();
  useEffect(() => {setOpen(false)}, [loc.pathname, setOpen]);
}

/** Pages outside a dataset (the datasets list). */
export function HomeLayout() {
  const [open, setOpen] = useState(false);
  useCloseOnNavigate(setOpen);
  return (
    <div className="shell">
      <MobileBar onMenu={() => setOpen(!open)} />
      {open && <div className="scrim" onClick={() => setOpen(false)} aria-hidden="true" />}
      <Sidebar open={open}>
        <NavLink to="/app" end className="nav-link">Datasets</NavLink>
      </Sidebar>
      <main className="main" id="main"><Outlet /></main>
    </div>
  );
}

/** Pages inside one dataset. */
export function DatasetLayout() {
  const id = useDatasetId();
  const ds = useDataset(id);
  const [open, setOpen] = useState(false);
  useCloseOnNavigate(setOpen);
  return (
    <div className="shell">
      <MobileBar onMenu={() => setOpen(!open)} />
      {open && <div className="scrim" onClick={() => setOpen(false)} aria-hidden="true" />}
      <Sidebar open={open}>
        <Link to="/app" className="ds-switch" title="All datasets">
          <span className="label">Dataset · switch</span>
          <span className="name">
            {ds.data?.name ?? "…"}{" "}
            {ds.data && <span className="mono xs muted" style={{ fontWeight: 400 }}>v{ds.data.current_version}</span>}
          </span>
          {ds.data?.sheet_name && <span className="xs muted">Sheet: {ds.data.sheet_name}</span>}
        </Link>
        {SECTIONS.map(([path, label]) => (
          <NavLink key={path} to={`/d/${id}/${path}`} className="nav-link">{label}</NavLink>
        ))}
      </Sidebar>
      <main className="main" id="main"><Outlet /></main>
    </div>
  );
}