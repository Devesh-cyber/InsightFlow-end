import { keepPreviousData, useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { FormulaBuilder } from "../components/RuleBuilder";
import { errorText, useToast } from "../components/Toast";
import { ErrorState, Loading, Note, PageHead, Skeleton, TypeChip } from "../components/ui";
import { useDataset, useDatasetId } from "../hooks/data";
import { api } from "../lib/api";
import { cell, num } from "../lib/format";
import { DATE_PARTS, STEP_HELP, STEP_LABEL, after, defaultStep, describe, kindOf, stepsFor, type Kind } from "../lib/mlsteps";
import type { ColumnInfo, MLColumnPlan, MLPlan, MLPreview, MLRole, MLStats, MLStep, MLSuggestion } from "../lib/types";

const EMPTY: MLPlan = { columns: {}, features: [], split: { enabled: false, test_size: 0.2, seed: 42, stratify: true } };
const NONE: MLColumnPlan = { role: "use", steps: [] };

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

const fmt = (x: number | null | undefined) =>
  x === null || x === undefined ? "—" : Math.abs(x) >= 1e5 || (Math.abs(x) < 1e-3 && x !== 0) ? x.toExponential(2) : num(Math.round(x * 1000) / 1000);

function statLine(s: MLStats | null): string {
  if (!s) return "new";
  if (s.mean !== undefined) return `mean ${fmt(s.mean)} · sd ${fmt(s.std)} · ${fmt(s.min)} to ${fmt(s.max)}`;
  return `${num(s.unique ?? 0)} distinct`;
}

/** One step's settings. */
function StepFields({ step, onChange, column }: { step: MLStep; onChange: (s: MLStep) => void; column: string }) {
  const sel = (label: string, value: string, options: [string, string][], set: (v: string) => void) => (
    <label className="field" style={{ flex: "1 1 160px" }}>{label}
      <select className="select" aria-label={`${column} ${label}`} value={value} onChange={(e) => set(e.target.value)}>
        {options.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
      </select>
    </label>
  );
  const numIn = (label: string, value: number | null | undefined, set: (v: number | null) => void, extra?: { min?: number; max?: number; step?: number }) => (
    <label className="field" style={{ flex: "1 1 90px" }}>{label}
      <input className="input" type="number" aria-label={`${column} ${label}`} value={value ?? ""} {...extra}
        onChange={(e) => set(e.target.value === "" ? null : Number(e.target.value))} />
    </label>
  );
  switch (step.kind) {
    case "fill_missing":
      return (
        <div className="row" style={{ gap: 8, alignItems: "flex-end" }}>
          {sel("With", step.strategy, [["median", "median"], ["mean", "mean"], ["mode", "most common"], ["constant", "a value"]], (v) => onChange({ ...step, strategy: v as typeof step.strategy }))}
          {step.strategy === "constant" && (
            <label className="field" style={{ flex: "1 1 120px" }}>Value
              <input className="input" aria-label={`${column} fill value`} value={String(step.value ?? "")} onChange={(e) => onChange({ ...step, value: e.target.value })} />
            </label>
          )}
          <label className="check small" style={{ flex: "1 1 100%" }}>
            <input type="checkbox" checked={!!step.add_indicator} onChange={(e) => onChange({ ...step, add_indicator: e.target.checked })} />
            Add a "{column}_was_missing" 0/1 column
          </label>
        </div>
      );
    case "clip":
      return (
        <div className="row" style={{ gap: 8, alignItems: "flex-end" }}>
          {sel("Limits", step.method, [["percentile", "percentiles"], ["iqr", "IQR fences"], ["values", "values I type"]], (v) =>
            onChange(v === "percentile" ? { kind: "clip", method: "percentile", lower: 1, upper: 99 } : v === "iqr" ? { kind: "clip", method: "iqr", factor: 1.5 } : { kind: "clip", method: "values", lower: null, upper: null }))}
          {step.method === "iqr" ? numIn("k", step.factor ?? 1.5, (v) => onChange({ ...step, factor: v ?? 1.5 }), { min: 0.5, max: 10, step: 0.5 }) : (
            <>
              {numIn(step.method === "percentile" ? "Lower %" : "Min", step.lower, (v) => onChange({ ...step, lower: v }))}
              {numIn(step.method === "percentile" ? "Upper %" : "Max", step.upper, (v) => onChange({ ...step, upper: v }))}
            </>
          )}
        </div>
      );
    case "transform":
      return sel("Transform", step.method, [["log1p", "log(1 + x): handles zeros"], ["log", "ln(x): above 0"], ["log10", "log10(x)"], ["sqrt", "√x"], ["square", "x²"]], (v) => onChange({ ...step, method: v as typeof step.method }));
    case "scale":
      return sel("Method", step.method, [["standard", "standard: mean 0, sd 1"], ["minmax", "min-max: 0 to 1"], ["robust", "robust: median / IQR (outliers)"]], (v) => onChange({ ...step, method: v as typeof step.method }));
    case "encode":
      return (
        <div className="row" style={{ gap: 8, alignItems: "flex-end" }}>
          {sel("Method", step.method, [["onehot", "one-hot: a 0/1 column per value"], ["frequency", "frequency: share of rows"], ["ordinal", "ordinal: rank 0, 1, 2…"]], (v) => onChange({ ...step, method: v as typeof step.method }))}
          {step.method === "onehot" && numIn("Max columns", step.max_categories ?? 20, (v) => onChange({ ...step, max_categories: v ?? 20 }), { min: 1, max: 50 })}
          {step.method === "onehot" && (
            <label className="check small"><input type="checkbox" checked={!!step.drop_first} onChange={(e) => onChange({ ...step, drop_first: e.target.checked })} />Drop the first column</label>
          )}
          {step.method === "ordinal" && (
            <label className="field" style={{ flex: "1 1 100%" }}>Order, lowest first (optional; default A→Z)
              <input className="input" aria-label={`${column} order`} placeholder="low, medium, high" defaultValue={(step.order ?? []).join(", ")}
                onChange={(e) => { const items = e.target.value.split(",").map((x) => x.trim()).filter(Boolean); onChange({ ...step, order: items.length ? items : null }); }} />
            </label>
          )}
        </div>
      );
    case "bin":
      return (
        <div className="row" style={{ gap: 8, alignItems: "flex-end" }}>
          {sel("Groups", step.method, [["quantile", "equal-count"], ["equal_width", "equal-width"]], (v) => onChange({ ...step, method: v as typeof step.method }))}
          {numIn("How many", step.bins, (v) => onChange({ ...step, bins: v ?? 5 }), { min: 2, max: 50 })}
        </div>
      );
    case "date_parts":
      return (
        <div className="options">
          {DATE_PARTS.map((p) => {
            const on = step.parts.includes(p);
            return (
              <button type="button" key={p} className={`option ${on ? "rec" : ""}`} aria-pressed={on}
                onClick={() => onChange({ ...step, parts: on ? step.parts.filter((x) => x !== p) : [...step.parts, p] })}>{p}</button>
            );
          })}
        </div>
      );
    case "to_number":
      return null;
  }
}

/** Role + ordered steps for one column. */
function ColumnEditor({ name, dtype, plan, suggestion, onChange }: {
  name: string; dtype: string; plan: MLColumnPlan; suggestion?: MLSuggestion; onChange: (p: MLColumnPlan) => void;
}) {
  let kind: Kind = kindOf(dtype);
  const kinds: Kind[] = [];
  for (const s of plan.steps) { kinds.push(kind); kind = after(s, kind); }
  const addable = stepsFor(kind);
  const [adding, setAdding] = useState("");
  const move = (i: number, d: number) => {
    const steps = [...plan.steps];
    const [s] = steps.splice(i, 1);
    steps.splice(i + d, 0, s);
    onChange({ ...plan, steps });
  };
  return (
    <div className="card stack" style={{ "--gap": "12px" } as React.CSSProperties}>
      <div className="row" style={{ gap: 8 }}>
        <span className="strong" style={{ fontSize: 16 }}>{name}</span>
        <TypeChip dtype={dtype} />
      </div>
      <div className="segmented" role="tablist" aria-label={`${name} role`}>
        {(["use", "drop", "target"] as MLRole[]).map((r) => (
          <button key={r} type="button" role="tab" aria-selected={plan.role === r} onClick={() => onChange({ ...plan, role: r })}>
            {r === "use" ? "Use as feature" : r === "drop" ? "Drop" : "Target (what to predict)"}
          </button>
        ))}
      </div>
      {suggestion && (suggestion.role_reason || suggestion.reason) && (
        <div className="xs muted">
          Suggestion: {suggestion.suggested_role === "drop" ? `drop (${suggestion.role_reason})` : suggestion.reason}
          {" "}
          <button type="button" className="link-btn" onClick={() => onChange({ role: plan.role === "target" ? "target" : suggestion.suggested_role, steps: suggestion.suggested_role === "drop" ? plan.steps : suggestion.steps })}>Use it</button>
        </div>
      )}
      {plan.role === "drop" ? <p className="small muted">This column won't be in the ML-ready file.</p> : (
        <>
          {plan.steps.length === 0 && <p className="small muted">No steps: the column goes in as it is.</p>}
          {plan.steps.map((s, i) => (
            <div key={i} className="stack" style={{ "--gap": "8px", padding: 12, border: "1px solid var(--line)", borderRadius: 10 } as React.CSSProperties}>
              <div className="row" style={{ gap: 6 }}>
                <span className="chip">{i + 1}</span>
                <span className="strong small" style={{ flex: 1 }}>{STEP_LABEL[s.kind]}</span>
                <button type="button" className="btn ghost sm" aria-label={`Move step ${i + 1} up`} disabled={i === 0} onClick={() => move(i, -1)}>↑</button>
                <button type="button" className="btn ghost sm" aria-label={`Move step ${i + 1} down`} disabled={i === plan.steps.length - 1} onClick={() => move(i, 1)}>↓</button>
                <button type="button" className="btn ghost sm" aria-label={`Remove step ${i + 1}`} onClick={() => onChange({ ...plan, steps: plan.steps.filter((_, j) => j !== i) })}>✕</button>
              </div>
              <StepFields step={s} column={name} onChange={(n) => onChange({ ...plan, steps: plan.steps.map((x, j) => (j === i ? n : x)) })} />
              {kinds[i] && !stepsFor(kinds[i]).includes(s.kind) && <span className="xs warn-text">This step doesn't fit here (the column isn't {s.kind === "date_parts" ? "a date" : "the right type"} at this point).</span>}
            </div>
          ))}
          {addable.length > 0 ? (
            <div className="row" style={{ gap: 8 }}>
              <select className="select" style={{ flex: 1 }} aria-label={`Add step to ${name}`} value={adding} onChange={(e) => setAdding(e.target.value)}>
                <option value="">Add a step…</option>
                {addable.map((k) => <option key={k} value={k}>{STEP_LABEL[k]}</option>)}
              </select>
              <button type="button" className="btn" disabled={!adding} onClick={() => { onChange({ ...plan, steps: [...plan.steps, defaultStep(adding as MLStep["kind"], kind)] }); setAdding(""); }}>Add</button>
            </div>
          ) : <p className="xs muted">This column has become several columns, so no more steps can follow.</p>}
          {adding && <p className="xs muted" style={{ margin: 0 }}>{STEP_HELP[adding as MLStep["kind"]]}</p>}
        </>
      )}
    </div>
  );
}

function PreviewPanel({ preview, error, loading }: { preview?: MLPreview; error: unknown; loading: boolean }) {
  if (error) return <Note tone="danger" title="This plan can't run yet.">{errorText(error)}</Note>;
  if (!preview) return <Skeleton height={200} />;
  const p = preview;
  return (
    <div className="stack" style={{ opacity: loading ? 0.6 : 1 }}>
      <div className="tiles">
        <div className="tile small"><div className="t-label">Rows</div><div className="t-value">{num(p.shape_before[0])} → {num(p.shape_after[0])}</div></div>
        <div className="tile small"><div className="t-label">Columns</div><div className="t-value">{p.shape_before[1]} → {p.shape_after[1]}</div></div>
        <div className="tile small"><div className="t-label">Target</div><div className="t-value" style={{ fontSize: 16 }}>{p.target ?? "none"}</div></div>
        {p.split && <div className="tile small"><div className="t-label">Split</div><div className="t-value" style={{ fontSize: 16 }}>{num(p.split.train)} train · {num(p.split.test)} test</div><div className="t-sub">{p.split.how}</div></div>}
      </div>
      {p.warnings.map((w) => <Note key={w}>{w}</Note>)}
      <div className="table-wrap" style={{ maxHeight: 420, overflow: "auto" }}>
        <table className="data">
          <thead><tr><th>Output column</th><th>From</th><th>Before</th><th>After</th><th className="r">Empty</th></tr></thead>
          <tbody>
            {p.columns.map((c) => (
              <tr key={c.name}>
                <td className="strong">{c.name}{c.name === p.target && <span className="chip" style={{ marginLeft: 6 }}>target</span>}</td>
                <td className="muted small">{c.source === c.name ? "" : c.source}</td>
                <td className="small muted">{statLine(c.before)}</td>
                <td className="small">{statLine(c.after)}</td>
                <td className={`r ${c.after.missing ? "warn-text" : ""}`}>{num(c.after.missing)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <h3 style={{ margin: "8px 0 0" }}>First rows of the ML-ready data</h3>
      <div className="table-wrap" style={{ maxHeight: 360, overflow: "auto" }}>
        <table className="data">
          <thead><tr>{p.preview.columns.map((c) => <th key={c}>{c}</th>)}</tr></thead>
          <tbody>
            {p.preview.rows.map((r, i) => (
              <tr key={i}>{r.map((v, j) => <td key={j} className="cell">{v === null ? <span className="null">missing</span> : typeof v === "number" ? fmt(v) : cell(v)}</td>)}</tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default function MLReady() {
  const id = useDatasetId();
  const ds = useDataset(id);
  const version = ds.data?.current_version;
  const { notify } = useToast();
  const view = useQuery({ queryKey: ["ds", id, version, "ml"], queryFn: () => api.mlView(id), enabled: version !== undefined, staleTime: Infinity });
  const [plan, setPlan] = useState<MLPlan | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [saveState, setSaveState] = useState<"saved" | "saving" | "error">("saved");
  const loadedFor = useRef<number | null>(null);

  useEffect(() => {
    if (view.data && loadedFor.current !== view.data.version) {
      loadedFor.current = view.data.version;
      setPlan({ ...EMPTY, ...view.data.plan, split: { ...EMPTY.split, ...view.data.plan.split } });
    }
  }, [view.data]);

  const locked = view.data?.readiness?.locked ?? true;

  // autosave (debounced); nothing is saved while the stage is locked
  const toSave = useDebounced(plan, 800);
  const firstSave = useRef(true);
  useEffect(() => {
    if (!toSave) return;
    if (firstSave.current) { firstSave.current = false; return; }
    if (locked) return;
    setSaveState("saving");
    api.mlSave(id, toSave).then(() => setSaveState("saved")).catch(() => setSaveState("error"));
  }, [toSave, id, locked]);

  const previewPlan = useDebounced(plan, 500);
  const preview = useQuery({
    queryKey: ["ml-preview", id, version, previewPlan],
    queryFn: () => api.mlPreview(id, previewPlan as MLPlan),
    enabled: !!previewPlan && version !== undefined && !locked,
    placeholderData: keepPreviousData,
    retry: false,
    staleTime: Infinity,
  });

  const download = useMutation({
    mutationFn: async () => { if (plan) await api.mlSave(id, plan); await api.mlExport(id); },
    onSuccess: () => notify(plan?.split.enabled ? "Downloaded train/test ZIP." : "Downloaded the ML-ready CSV."),
    onError: (e) => notify(errorText(e)),
  });

  const columns: { name: string; dtype: string; feature?: boolean }[] = useMemo(() => [
    ...(ds.data?.columns ?? []).map((c) => ({ name: c.name, dtype: c.dtype })),
    ...(plan?.features ?? []).map((f) => ({ name: f.name, dtype: "float", feature: true })),
  ], [ds.data, plan?.features]);
  const numeric: ColumnInfo[] = (ds.data?.columns ?? []).filter((c) => c.dtype === "integer" || c.dtype === "float");

  if (ds.isPending || view.isPending || !plan) return <Loading />;
  if (ds.isError) return <ErrorState error={ds.error} retry={() => ds.refetch()} />;
  if (view.isError) return <ErrorState error={view.error} retry={() => view.refetch()} />;

  const sugg = view.data.suggestions;
  const ready = view.data.readiness;
  const colPlan = (name: string) => plan.columns[name] ?? NONE;
  const setCol = (name: string, p: MLColumnPlan) => {
    const next = { ...plan.columns };
    if (p.role === "target") for (const [k, v] of Object.entries(next)) if (v.role === "target" && k !== name) next[k] = { ...v, role: "use" };
    next[name] = p;
    setPlan({ ...plan, columns: next });
  };
  const target = Object.entries(plan.columns).find(([, v]) => v.role === "target")?.[0] ?? "";
  const current = selected && columns.some((c) => c.name === selected) ? selected : columns[0]?.name;
  const currentCol = columns.find((c) => c.name === current);

  const applyAll = () => {
    const next: Record<string, MLColumnPlan> = {};
    for (const c of columns) {
      const s = sugg[c.name];
      const existing = plan.columns[c.name];
      if (existing?.role === "target") { next[c.name] = existing; continue; }
      next[c.name] = s ? { role: s.suggested_role === "target" ? "use" : s.suggested_role, steps: s.suggested_role === "drop" ? [] : s.steps } : existing ?? NONE;
    }
    setPlan({ ...plan, columns: next });
    notify("Applied the suggestions. Change anything you like; nothing is downloaded until you choose.");
  };

  return (
    <>
      <PageHead
        title="ML-ready dataset"
        sub="Your rules for each column: keep or drop it, then add steps in the order you want. Your cleaned dataset isn't changed; this builds a separate file to train models on."
        actions={locked ? <span className="chip">locked</span> : <span className="xs muted" aria-live="polite">{saveState === "saving" ? "Saving…" : saveState === "error" ? "Couldn't save" : "Saved"}</span>}
      />
      {locked && (
        <div className="card stack locked-banner" role="status" style={{ "--gap": "10px" } as React.CSSProperties}>
          <div className="strong" style={{ fontSize: 16 }}>Clean the data first</div>
          <p className="small muted" style={{ margin: 0 }}>
            The ML-ready dataset is built from your cleaned data, so this stage unlocks when cleaning is done. You can look around,
            but nothing can be changed or downloaded yet. Items you mark "looks fine, keep it" count as done.
          </p>
          <ul className="small" style={{ margin: 0, paddingLeft: 18 }}>
            <li>Primary cleaning: {ready.primary_open === 0 ? "done ✓" : `${ready.primary_open} left`}</li>
            <li>Explore & review: {ready.secondary_open === 0 ? "done ✓" : `${ready.secondary_open} left`}</li>
          </ul>
          <div><Link className="btn primary" to={`/d/${id}/clean`}>Go to Clean →</Link></div>
        </div>
      )}
      <fieldset disabled={locked} className={`ml-body ${locked ? "is-locked" : ""}`} aria-disabled={locked}>

      <section className="card row" style={{ gap: 16, alignItems: "flex-end" }} aria-label="Target and split">
        <label className="field" style={{ flex: "1 1 220px" }}>Target column (what a model will predict)
          <select className="select" aria-label="Target column" value={target} onChange={(e) => {
            const next = { ...plan.columns };
            for (const [k, v] of Object.entries(next)) if (v.role === "target") next[k] = { ...v, role: "use" };
            if (e.target.value) next[e.target.value] = { ...(next[e.target.value] ?? NONE), role: "target" };
            setPlan({ ...plan, columns: next });
          }}>
            <option value="">None (just prepare the features)</option>
            {columns.map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}
          </select>
        </label>
        <label className="check" style={{ flex: "0 1 auto" }}>
          <input type="checkbox" checked={plan.split.enabled} onChange={(e) => setPlan({ ...plan, split: { ...plan.split, enabled: e.target.checked } })} />
          Split into train and test
        </label>
        {plan.split.enabled && (
          <>
            <label className="field" style={{ flex: "0 1 120px" }}>Test share %
              <input className="input" type="number" min={5} max={50} aria-label="Test share" value={Math.round(plan.split.test_size * 100)}
                onChange={(e) => setPlan({ ...plan, split: { ...plan.split, test_size: Math.min(50, Math.max(5, Number(e.target.value) || 20)) / 100 } })} />
            </label>
            <label className="field" style={{ flex: "0 1 110px" }}>Seed
              <input className="input" type="number" min={0} aria-label="Seed" value={plan.split.seed}
                onChange={(e) => setPlan({ ...plan, split: { ...plan.split, seed: Math.max(0, Number(e.target.value) || 0) } })} />
            </label>
            {target && (
              <label className="check small"><input type="checkbox" checked={plan.split.stratify} onChange={(e) => setPlan({ ...plan, split: { ...plan.split, stratify: e.target.checked } })} />Keep target proportions (stratify)</label>
            )}
          </>
        )}
        <p className="xs muted" style={{ flex: "1 1 100%", margin: 0 }}>
          {plan.split.enabled
            ? "Steps that learn from the data (fill values, clip limits, scaling, encodings, bins) learn from the train rows only, so test rows don't leak into training."
            : "Without a split, steps that learn from the data use every row. Fine for exploring; for an honest test score, turn the split on."}
        </p>
      </section>

      <div className="row" style={{ gap: 8 }}>
        <button className="btn" onClick={applyAll}>Apply all suggestions</button>
        <button className="btn ghost" onClick={() => setPlan({ ...plan, columns: {}, features: [] })}>Clear all rules</button>
        <span className="grow" />
        <button className="btn primary" disabled={locked || download.isPending || !!preview.error} onClick={() => download.mutate()}>
          {download.isPending && <span className="spinner" />} {plan.split.enabled ? "Download train/test (ZIP)" : "Download ML-ready CSV"}
        </button>
      </div>

      <div className="row" style={{ alignItems: "flex-start", gap: 20 }}>
        <section style={{ flex: "1 1 300px", minWidth: 0 }} className="stack" aria-label="Columns">
          <h2>Columns</h2>
          <div className="stack" style={{ "--gap": "4px" } as React.CSSProperties}>
            {columns.map((c) => {
              const p = colPlan(c.name);
              return (
                <button key={c.name} type="button" className={`ml-col ${c.name === current ? "active" : ""} ${p.role === "drop" ? "dropped" : ""}`} onClick={() => setSelected(c.name)}>
                  <span className="row" style={{ gap: 6, flexWrap: "nowrap" }}>
                    <span className="strong" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{c.name}</span>
                    {c.feature && <span className="chip">new</span>}
                    {p.role === "target" && <span className="chip" style={{ color: "var(--accent-strong)" }}>target</span>}
                    <span className="mono xs muted" style={{ marginLeft: "auto" }}>{c.dtype}</span>
                  </span>
                  <span className="xs muted" style={{ textAlign: "left" }}>
                    {p.role === "drop" ? "dropped" : p.steps.length ? p.steps.map(describe).join(" → ") : "as is"}
                  </span>
                </button>
              );
            })}
          </div>

          <h2 style={{ marginTop: 12 }}>New features</h2>
          <p className="xs muted" style={{ margin: 0 }}>Make a column from others, e.g. price_per_unit = total ÷ units. It then appears above, where you can add steps to it.</p>
          {plan.features.map((f, i) => (
            <div key={i} className="card stack" style={{ "--gap": "8px", padding: 12 } as React.CSSProperties}>
              <div className="row" style={{ gap: 8 }}>
                <input className="input" style={{ flex: 1 }} aria-label={`Feature ${i + 1} name`} value={f.name}
                  onChange={(e) => setPlan({ ...plan, features: plan.features.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)) })} />
                <button type="button" className="btn ghost sm" aria-label={`Remove feature ${i + 1}`} onClick={() => setPlan({ ...plan, features: plan.features.filter((_, j) => j !== i) })}>✕</button>
              </div>
              <FormulaBuilder label={`Feature ${i + 1} formula`} terms={f.formula} columns={numeric}
                onChange={(formula) => setPlan({ ...plan, features: plan.features.map((x, j) => (j === i ? { ...x, formula } : x)) })} />
            </div>
          ))}
          <div>
            <button type="button" className="link-btn" disabled={numeric.length === 0}
              onClick={() => setPlan({ ...plan, features: [...plan.features, { name: `feature_${plan.features.length + 1}`, formula: [{ column: numeric[0]?.name ?? "" }] }] })}>
              + New feature
            </button>
          </div>
        </section>

        <section style={{ flex: "1.4 1 380px", minWidth: 0 }} className="stack" aria-label="Column rules">
          <h2>Rules for this column</h2>
          {currentCol && (
            <ColumnEditor key={currentCol.name} name={currentCol.name} dtype={currentCol.dtype} plan={colPlan(currentCol.name)}
              suggestion={sugg[currentCol.name]} onChange={(p) => setCol(currentCol.name, p)} />
          )}
        </section>
      </div>

      <section className="stack" aria-label="Preview">
        <h2>Preview {preview.isFetching && <span className="spinner" style={{ marginLeft: 8 }} />}</h2>
        {locked ? <p className="small muted">The preview appears once cleaning is done.</p> : (
          <PreviewPanel preview={preview.data} error={preview.error} loading={preview.isFetching} />
        )}
      </section>
      </fieldset>
    </>
  );
}
