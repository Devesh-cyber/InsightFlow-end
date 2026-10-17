import { useMemo, useState } from "react";
import { OP_FORMS, cleanParams, initialParams, missingInput, visible, type Field, type OpForm } from "../lib/operations";
import type { ColumnInfo, Operation } from "../lib/types";

function ColumnsPicker({ field, columns, value, onChange }: {
  field: Extract<Field, { kind: "columns" }>; columns: ColumnInfo[]; value: string[] | null; onChange: (v: string[] | null) => void;
}) {
  const options = field.types ? columns.filter((c) => field.types!.includes(c.dtype)) : columns;
  const selected = new Set(value ?? []);
  const toggle = (name: string) => {
    const next = new Set(selected);
    if (next.has(name)) next.delete(name);
    else next.add(name);
    onChange(next.size ? [...next] : field.optional ? null : []);
  };
  return (
    <fieldset className="field" style={{ border: "none", padding: 0, margin: 0 }}>
      <legend style={{ marginBottom: 6 }}>{field.label}{field.hint && <span style={{ fontWeight: 400 }}> · {field.hint}</span>}</legend>
      {options.length === 0 ? <span className="small muted">No columns of a suitable type.</span> : (
        <div className="options">
          {options.map((c) => (
            <button type="button" key={c.name} className={`option ${selected.has(c.name) ? "rec" : ""}`} aria-pressed={selected.has(c.name)} onClick={() => toggle(c.name)}>
              {c.name} <span className="mono xs" style={{ fontWeight: 400, opacity: 0.8 }}>{c.dtype}</span>
            </button>
          ))}
        </div>
      )}
      {options.length > 1 && (
        <div className="row" style={{ gap: 12, marginTop: 6 }}>
          <button type="button" className="link-btn" onClick={() => onChange(options.map((c) => c.name))}>Select all</button>
          {selected.size > 0 && <button type="button" className="link-btn" style={{ color: "var(--muted)" }} onClick={() => onChange(field.optional ? null : [])}>Clear</button>}
        </div>
      )}
    </fieldset>
  );
}

function FieldInput({ field, columns, params, set }: {
  field: Field; columns: ColumnInfo[]; params: Record<string, unknown>; set: (k: string, v: unknown) => void;
}) {
  const v = params[field.key];
  switch (field.kind) {
    case "columns":
      return <ColumnsPicker field={field} columns={columns} value={v as string[] | null} onChange={(x) => set(field.key, x)} />;
    case "column": {
      const options = field.types ? columns.filter((c) => field.types!.includes(c.dtype)) : columns;
      return (
        <label className="field">{field.label}
          <select className="select" aria-label={field.label} value={String(v ?? "")} onChange={(e) => set(field.key, e.target.value)}>
            <option value="">Choose a column…</option>
            {options.map((c) => <option key={c.name} value={c.name}>{c.name} ({c.dtype})</option>)}
          </select>
        </label>
      );
    }
    case "select":
      return (
        <label className="field">{field.label}
          <select className="select" aria-label={field.label} value={String(v)} onChange={(e) => set(field.key, e.target.value)}>
            {field.options.map(([val, label]) => <option key={val} value={val}>{label}</option>)}
          </select>
        </label>
      );
    case "text":
      return (
        <label className="field">{field.label}
          <input className="input" value={String(v ?? "")} placeholder={field.placeholder} onChange={(e) => set(field.key, e.target.value)} />
        </label>
      );
    case "number":
      return (
        <label className="field">{field.label}
          <input className="input" type="number" value={Number(v)} min={field.min} max={field.max} step={field.step} style={{ maxWidth: 160 }}
            onChange={(e) => set(field.key, e.target.value === "" ? field.default : Number(e.target.value))} />
        </label>
      );
    case "list":
      return (
        <label className="field">{field.label}{field.hint && ` · ${field.hint}`}
          <input className="input" placeholder={field.placeholder} defaultValue={(v as string[]).join(", ")}
            onChange={(e) => set(field.key, e.target.value.split(",").map((s) => s.trim()).filter(Boolean))} />
          <span className="xs" style={{ fontWeight: 400 }}>Separate values with commas.</span>
        </label>
      );
    case "bool":
      return (
        <label className="check"><input type="checkbox" checked={Boolean(v)} onChange={(e) => set(field.key, e.target.checked)} />{field.label}</label>
      );
    case "rename": {
      const mapping = (v as Record<string, string>) ?? {};
      return (
        <fieldset className="field" style={{ border: "none", padding: 0, margin: 0 }}>
          <legend style={{ marginBottom: 6 }}>{field.label} · leave blank to keep a name</legend>
          <div className="stack" style={{ "--gap": "6px", maxHeight: 280, overflowY: "auto" } as React.CSSProperties}>
            {columns.map((c) => (
              <div key={c.name} className="row" style={{ gap: 8, flexWrap: "nowrap" }}>
                <span className="small" style={{ flex: "0 0 40%", overflow: "hidden", textOverflow: "ellipsis", color: "var(--text)" }}>{c.name}</span>
                <span className="muted">→</span>
                <input className="input" style={{ height: 34, flex: 1 }} value={mapping[c.name] ?? ""} placeholder={c.name} aria-label={`New name for ${c.name}`}
                  onChange={(e) => set(field.key, { ...mapping, [c.name]: e.target.value })} />
              </div>
            ))}
          </div>
        </fieldset>
      );
    }
  }
}

/** Pick any operation, fill a short form, send it to preview. */
export function OperationBuilder({ columns, onPreview }: { columns: ColumnInfo[]; onPreview: (op: Operation, label: string) => void }) {
  const [type, setType] = useState(OP_FORMS[0].type);
  const form = OP_FORMS.find((f) => f.type === type) as OpForm;
  const [params, setParams] = useState<Record<string, unknown>>(() => initialParams(form));
  const groups = useMemo(() => [...new Set(OP_FORMS.map((f) => f.group))], []);
  const problem = missingInput(form, params);

  const choose = (t: string) => {
    const f = OP_FORMS.find((x) => x.type === t) as OpForm;
    setType(t);
    setParams(initialParams(f));
  };

  return (
    <form className="card stack" style={{ "--gap": "16px" } as React.CSSProperties}
      onSubmit={(e) => { e.preventDefault(); if (!problem) onPreview({ type, params: cleanParams(form, params) }, form.label); }}>
      <label className="field">Step
        <select className="select" aria-label="Step" value={type} onChange={(e) => choose(e.target.value)}>
          {groups.map((g) => (
            <optgroup key={g} label={g}>
              {OP_FORMS.filter((f) => f.group === g).map((f) => <option key={f.type} value={f.type}>{f.label}</option>)}
            </optgroup>
          ))}
        </select>
      </label>
      <p className="small muted">{form.help}</p>
      {form.fields.filter((f) => visible(f, params)).map((f) => (
        <FieldInput key={type + f.key} field={f} columns={columns} params={params} set={(k, v) => setParams((p) => ({ ...p, [k]: v }))} />
      ))}
      <div className="row">
        <button className="btn primary" disabled={!!problem}>Preview →</button>
        {problem && <span className="xs muted">{problem}</span>}
      </div>
    </form>
  );
}
