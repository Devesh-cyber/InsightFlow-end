import { createContext, useCallback, useContext, useState, type ReactNode } from "react";
import { ApiError } from "../lib/api";

interface Toast {
  id: number;
  kind: "ok" | "error";
  text: string;
}

const ToastContext = createContext<{ notify: (text: string) => void; fail: (error: unknown) => void }>({
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
  const push = useCallback((kind: Toast["kind"], text: string) => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t.slice(-3), { id, kind, text }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), kind === "error" ? 8000 : 4000);
  }, []);
  const notify = useCallback((text: string) => push("ok", text), [push]);
  const fail = useCallback((error: unknown) => push("error", errorText(error)), [push]);

  return (
    <ToastContext.Provider value={{ notify, fail }}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`toast ${t.kind === "error" ? "error" : ""}`}>
            <span>{t.text}</span>
            <button aria-label="Dismiss" onClick={() => setToasts((all) => all.filter((x) => x.id !== t.id))}>×</button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export const useToast = () => useContext(ToastContext);
