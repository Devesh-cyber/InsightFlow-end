import { createContext, useCallback, useContext, useState, type ReactNode } from "react";
import { ApiError } from "../lib/api";

export interface ToastAction {
  label: string;
  run: () => void;
}

interface Toast {
  id: number;
  kind: "ok" | "error";
  text: string;
  action?: ToastAction;
}

const ToastContext = createContext<{ notify: (text: string, action?: ToastAction) => void; fail: (error: unknown) => void }>({
  notify: () => {},
  fail: () => {},
});

export function errorText(error: unknown): string {
  if (error instanceof ApiError) {
    return error.requestId && error.status >= 500 ? `${error.message} (ref ${error.requestId.slice(0, 8)})` : error.message;
  }
  return error instanceof Error ? error.message : "Something went wrong.";
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const push = useCallback((kind: Toast["kind"], text: string, action?: ToastAction) => {
    const id = Date.now() + Math.random();
    // only the newest action stays clickable (an older "Undo" would undo the wrong step)
    setToasts((t) => [...t.slice(-3).map((x) => (action ? { ...x, action: undefined } : x)), { id, kind, text, action }]);
    // a toast with a button (e.g. Undo) stays longer so there's time to use it
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), kind === "error" || action ? 9000 : 4000);
  }, []);
  const notify = useCallback((text: string, action?: ToastAction) => push("ok", text, action), [push]);
  const fail = useCallback((error: unknown) => push("error", errorText(error)), [push]);

  return (
    <ToastContext.Provider value={{ notify, fail }}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`toast ${t.kind === "error" ? "error" : ""}`}>
            <span>{t.text}</span>
            {t.action && (
              <button className="toast-action" onClick={() => { t.action!.run(); setToasts((all) => all.filter((x) => x.id !== t.id)); }}>
                {t.action.label}
              </button>
            )}
            <button aria-label="Dismiss" onClick={() => setToasts((all) => all.filter((x) => x.id !== t.id))}>×</button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export const useToast = () => useContext(ToastContext);
