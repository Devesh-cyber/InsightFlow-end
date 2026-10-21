import { useState } from "react";
import {
  ACTIONS, OP_TEXT, TRANSFORMS, actionProblem, conditionProblem, emptyCondition, isTextOp, opsFor, toCondition, toOperation, valueShape,
  type DraftAction, type DraftCondition, type RuleAction,
  isNumberTerm,
} from "../lib/rules";
import type { ColumnInfo, FormulaOperand, FormulaTerm, Operation, RuleCondition } from "../lib/types";
import { ShowRows } from "./MatchingRowsTable";

const OPERATORS: [string, string][] = [["+", "+"], ["-", "−"], ["*", "×"], ["/", "÷"]];

/** value (op value)…  — numbers and number columns only. */
export function FormulaBuilder({ terms, columns, onChange, label }: {
  terms: FormulaTerm[]; columns: ColumnInfo[]; onChange: (t: FormulaTerm[]) => void; label: string;
}) {
  const numeric = columns.filter((c) => c.dtype === "integer" || c.dtype === "float");
  const list = terms.length ? terms : [{ column: numeric[0]?.name ?? "" } as FormulaOperand];
  const set = (i: number, t: FormulaTerm) => onChange(list.map((x, j) => (j === i ? t : x)));
  return (
    <div className="stack" style={{ "--gap": "6px" } as React.CSSProperties} role="group" aria-label={label}>
      <div className="row" style={{ gap: 6 }}>
        {list.map((t, i) => {
          if (typeof t === "string") {
            return (
              <select key={i} className="select" style={{ width: 64 }} aria-label={`Operator ${(i + 1) / 2}`} value={t}
                onChange={(e) => set(i, e.target.value as FormulaTerm)}>
                {OPERATORS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
            );
          }
          const isNumber = isNumberTerm(t);
          return (
            <span key={i} className="row" style={{ gap: 4, flexWrap: "nowrap" }}>
              <select className="select" style={{ maxWidth: 170 }} aria-label={`Formula value ${i / 2 + 1}`}
                value={isNumber ? "__number" : (t as { column: string }).column}
                onChange={(e) => set(i, e.target.value === "__number" ? { number: 1 } : { column: e.target.value })}>
                {numeric.map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}
                <option value="__number">a number…</option>
              </select>
              {isNumber && (
                <input className="input" type="number" style={{ width: 90 }} aria-label={`Number ${i / 2 + 1}`}
                  value={Number.isFinite((t as { number: number }).number) ? (t as { number: number }).number : ""}
                  onChange={(e) => set(i, { number: e.target.value === "" ? NaN : Number(e.target.value) })} />
              )}
            </span>
          );
        })}
      </div>
      <div className="row" style={{ gap: 12 }}>
        {list.length < 19 && (
          <button type="button" className="link-btn" onClick={() => onChange([...list, "*", { column: numeric[0]?.name ?? "" }])}>+ add a part</button>
        )}
        {list.length > 1 && (
          <button type="button" className="link-btn" style={{ color: "var(--muted)" }} onClick={() => onChange(list.slice(0, -2))}>remove last part</button>
        )}
      </div>
    </div>
  );
}

function ConditionRow({ c, index, columns, onChange, onRemove }: {
  c: DraftCondition; index: number; columns: ColumnInfo[]; onChange: (c: DraftCondition) => void; onRemove?: () => void;
}) {
  const col = columns.find((x) => x.name === c.column);
  const ops = opsFor(col?.dtype);
  const shape = valueShape(c.op);
  const inputType = col?.dtype === "integer" || col?.dtype === "float" ? "number" : col?.dtype === "date" || col?.dtype === "datetime" ? "date" : "text";
  const n = index + 1;
  return (
    <div className="stack" style={{ "--gap": "8px", padding: "12px", border: "1px solid var(--line)", borderRadius: 10 } as React.CSSProperties}>
      <div className="row" style={{ gap: 8 }}>
        <select className="select" style={{ flex: "1 1 140px" }} aria-label={`Condition ${n} column`} value={c.column}
          onChange={(e) => {
            const next = columns.find((x) => x.name === e.target.value);
            const allowed = opsFor(next?.dtype);
            onChange({ ...c, column: e.target.value, op: allowed.includes(c.op) ? c.op : allowed[0], value: "", value2: "", formula: [] });
          }}>
          {columns.map((x) => <option key={x.name} value={x.name}>{x.name} ({x.dtype})</option>)}
        </select>
        <select className="select" style={{ flex: "1 1 150px" }} aria-label={`Condition ${n} operator`} value={c.op}
          onChange={(e) => onChange({ ...c, op: e.target.value as DraftCondition["op"] })}>
          {ops.map((o) => <option key={o} value={o}>{OP_TEXT[o]}</option>)}
        </select>
        {onRemove && <button type="button" className="btn ghost" aria-label={`Remove condition ${n}`} onClick={onRemove}>✕</button>}
      </div>
      {col?.dtype === "boolean" && shape === "one" ? (
        <select className="select" aria-label={`Condition ${n} value`} value={c.value} onChange={(e) => onChange({ ...c, value: e.target.value })}>
          <option value="">Choose…</option><option value="true">true</option><option value="false">false</option>
        </select>
      ) : shape === "one" ? (
        <input className="input" type={inputType} aria-label={`Condition ${n} value`} value={c.value} placeholder="value"
          onChange={(e) => onChange({ ...c, value: e.target.value })} />
      ) : shape === "two" ? (
        <div className="row" style={{ gap: 8, flexWrap: "nowrap" }}>
          <input className="input" type={inputType} aria-label={`Condition ${n} from`} value={c.value} placeholder="from" onChange={(e) => onChange({ ...c, value: e.target.value })} />
          <span className="muted small">and</span>
          <input className="input" type={inputType} aria-label={`Condition ${n} to`} value={c.value2} placeholder="to" onChange={(e) => onChange({ ...c, value2: e.target.value })} />
        </div>
      ) : shape === "list" ? (
        <input className="input" aria-label={`Condition ${n} values`} value={c.value} placeholder="a, b, c" onChange={(e) => onChange({ ...c, value: e.target.value })} />
      ) : shape === "formula" ? (
        <FormulaBuilder label={`Condition ${n} formula`} terms={c.formula} columns={columns} onChange={(formula) => onChange({ ...c, formula })} />
      ) : null}
      {col?.dtype === "text" && isTextOp(c.op) && (
        <label className="check small"><input type="checkbox" checked={c.ignoreCase} onChange={(e) => onChange({ ...c, ignoreCase: e.target.checked })} />Ignore capital letters</label>
      )}
    </div>
  );
}

/** Conditions + AND/OR. Shared by custom rules and saved checks. */
export function ConditionsEditor({ conditions, combine, columns, onChange, onCombine, optional }: {
  conditions: DraftCondition[]; combine: "and" | "or"; columns: ColumnInfo[];
  onChange: (c: DraftCondition[]) => void; onCombine: (c: "and" | "or") => void; optional?: boolean;
}) {
  return (
    <div className="stack" style={{ "--gap": "8px" } as React.CSSProperties}>
      {conditions.length > 1 && (
        <div className="row" style={{ gap: 8 }}>
          <span className="small muted">Match</span>
          <div className="segmented" role="tablist" aria-label="Combine conditions">
            <button type="button" role="tab" aria-selected={combine === "and"} onClick={() => onCombine("and")}>all (AND)</button>
            <button type="button" role="tab" aria-selected={combine === "or"} onClick={() => onCombine("or")}>any (OR)</button>
          </div>
        </div>
      )}
      {conditions.length === 0 && optional && <span className="small muted">No conditions: the action applies to every row.</span>}
      {conditions.map((c, i) => (
        <ConditionRow key={i} index={i} c={c} columns={columns}
          onChange={(next) => onChange(conditions.map((x, j) => (j === i ? next : x)))}
          onRemove={conditions.length > 1 || optional ? () => onChange(conditions.filter((_, j) => j !== i)) : undefined} />
      ))}
      {conditions.length < 10 && (
        <div><button type="button" className="link-btn" onClick={() => onChange([...conditions, emptyCondition(columns)])}>+ add condition</button></div>
      )}
    </div>
  );
}

export function conditionsProblem(conditions: DraftCondition[], columns: ColumnInfo[]): string | null {
  for (const [i, c] of conditions.entries()) {
    const p = conditionProblem(c, columns);
    if (p) return conditions.length > 1 ? `Condition ${i + 1}: ${p}` : p;
  }
  return null;
}

/** "If <conditions> then <action>" → preview, or save the conditions as a check. */
export function RuleBuilder({ columns, onPreview, onSaveCheck, saving }: {
  columns: ColumnInfo[];
  onPreview: (op: Operation, label: string) => void;
  onSaveCheck: (name: string, conditions: RuleCondition[], combine: "and" | "or") => void;
  saving?: boolean;
}) {
  const [conditions, setConditions] = useState<DraftCondition[]>(() => [emptyCondition(columns)]);
  const [combine, setCombine] = useState<"and" | "or">("and");
  const [action, setAction] = useState<DraftAction>({ action: "remove_rows", target: columns[0]?.name ?? "", value: "", min: "", max: "", formula: [], method: "log1p", keepOriginal: true, newName: "" });
  const [checkName, setCheckName] = useState("");
  const spec = ACTIONS.find((a) => a.value === action.action)!;
  const numeric = columns.filter((c) => c.dtype === "integer" || c.dtype === "float");
  const targets = action.action === "clamp" || action.action === "calculate" || action.action === "transform" ? numeric : columns;
  const problem = spec.noConditions ? actionProblem(action, columns, 0) : conditionsProblem(conditions, columns) ?? actionProblem(action, columns, conditions.length);
  const checkProblem = conditions.length === 0 ? "Add a condition." : conditionsProblem(conditions, columns) ?? (checkName.trim() ? null : "Name the check.");

  const chooseAction = (value: RuleAction) => {
    const needsNumber = value === "clamp" || value === "calculate" || value === "transform";
    const ok = needsNumber ? numeric.some((c) => c.name === action.target) : true;
    const firstCondition = conditions[0]?.column;
    setAction({ ...action, action: value, target: ok ? action.target || firstCondition || "" : numeric[0]?.name ?? "" });
  };

  return (
    <form className="card stack" style={{ "--gap": "14px" } as React.CSSProperties}
      onSubmit={(e) => { e.preventDefault(); if (!problem) onPreview(toOperation(conditions, combine, action, columns), spec.noConditions ? "Transform" : "Custom rule"); }}>
      {spec.noConditions ? (
        <div>
          <div className="strong">Whole column</div>
          <p className="xs muted" style={{ marginTop: 2 }}>A transform applies to every row: transforming only some rows would mix two scales in one column.</p>
        </div>
      ) : (
        <>
          <div>
            <div className="strong">If a row matches</div>
            <p className="xs muted" style={{ marginTop: 2 }}>Empty cells never match a comparison; use "is empty" to find them.</p>
          </div>
          <ConditionsEditor conditions={conditions} combine={combine} columns={columns} onChange={setConditions} onCombine={setCombine} optional={spec.conditionsOptional} />
        </>
      )}
      {!spec.noConditions && conditions.length > 0 && !conditionsProblem(conditions, columns) && (
        <ShowRows label="Show matching rows" emptyText="No rows match these conditions."
          match={{ conditions: conditions.map((c) => toCondition(c, columns)), combine }} />
      )}

      <label className="field">Then
        <select className="select" aria-label="Then" value={action.action} onChange={(e) => chooseAction(e.target.value as RuleAction)}>
          {ACTIONS.map((a) => <option key={a.value} value={a.value}>{a.label}</option>)}
        </select>
      </label>
      {spec.needsTarget && (
        <label className="field">Column to change
          <select className="select" aria-label="Column to change" value={action.target} onChange={(e) => setAction({ ...action, target: e.target.value })}>
            <option value="">Choose a column…</option>
            {targets.map((c) => <option key={c.name} value={c.name}>{c.name} ({c.dtype})</option>)}
          </select>
        </label>
      )}
      {action.action === "set_value" && (
        <label className="field">New value
          <input className="input" aria-label="New value" value={action.value} onChange={(e) => setAction({ ...action, value: e.target.value })} />
        </label>
      )}
      {action.action === "clamp" && (
        <div className="row" style={{ gap: 8 }}>
          <label className="field" style={{ flex: 1 }}>Minimum
            <input className="input" type="number" aria-label="Minimum" value={action.min} onChange={(e) => setAction({ ...action, min: e.target.value })} />
          </label>
          <label className="field" style={{ flex: 1 }}>Maximum
            <input className="input" type="number" aria-label="Maximum" value={action.max} onChange={(e) => setAction({ ...action, max: e.target.value })} />
          </label>
        </div>
      )}
      {action.action === "calculate" && (
        <div className="field">
          <span>{action.target || "Column"} =</span>
          <FormulaBuilder label="Calculation" terms={action.formula} columns={columns} onChange={(formula) => setAction({ ...action, formula })} />
        </div>
      )}
      {action.action === "transform" && (
        <>
          <label className="field">Transform
            <select className="select" aria-label="Transform" value={action.method ?? "log1p"} onChange={(e) => setAction({ ...action, method: e.target.value })}>
              {TRANSFORMS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </label>
          <label className="check small">
            <input type="checkbox" checked={action.keepOriginal !== false} onChange={(e) => setAction({ ...action, keepOriginal: e.target.checked })} />
            Keep the original column (add a new one)
          </label>
          {action.keepOriginal !== false && (
            <label className="field">New column name (optional)
              <input className="input" aria-label="New column name" value={action.newName ?? ""}
                placeholder={action.target ? `${action.target}_${{ log1p: "log", log: "ln", log10: "log10", sqrt: "sqrt" }[action.method ?? "log1p"]}` : "e.g. amount_log"}
                onChange={(e) => setAction({ ...action, newName: e.target.value })} />
            </label>
          )}
          <p className="xs muted">Calculated row by row, so it's safe before a train/test split. Values outside the transform's range become missing (the preview tells you how many).</p>
        </>
      )}
      {action.action === "clamp" && <p className="xs muted">Values outside the limits are set to the nearest limit. These are estimates, and the report marks them.</p>}

      <div className="row">
        <button className="btn primary" disabled={!!problem}>Preview →</button>
        {problem && <span className="xs muted">{problem}</span>}
      </div>

      <div className="stack" style={{ "--gap": "8px", borderTop: "1px solid var(--line)", paddingTop: 14 } as React.CSSProperties}>
        <div className="strong small">Or save the conditions as a check</div>
        <p className="xs muted">A check doesn't change data. Rows matching the conditions count as problems in Health and in the report, on every version.</p>
        <div className="row" style={{ gap: 8 }}>
          <input className="input" style={{ flex: "1 1 180px" }} aria-label="Check name" placeholder="e.g. Age between 0 and 120" maxLength={80}
            value={checkName} onChange={(e) => setCheckName(e.target.value)} />
          <button type="button" className="btn" disabled={!!checkProblem || saving}
            onClick={() => { onSaveCheck(checkName.trim(), conditions.map((c) => toCondition(c, columns)), combine); setCheckName(""); }}>
            {saving && <span className="spinner" />} Save check
          </button>
        </div>
        {checkProblem && checkName && <span className="xs muted">{checkProblem}</span>}
      </div>
    </form>
  );
}
