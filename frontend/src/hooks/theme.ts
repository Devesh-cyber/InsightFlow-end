import { useCallback, useEffect, useState } from "react";

export type ThemeChoice = "system" | "light" | "dark";
const KEY = "if-theme";

function read(): ThemeChoice {
  try {
    const v = localStorage.getItem(KEY);
    return v === "light" || v === "dark" ? v : "system";
  } catch {
    return "system";
  }
}

/** Light / dark / follow the OS. Stored per browser; applied as html[data-theme]. */
export function useTheme() {
  const [choice, setChoice] = useState<ThemeChoice>(read);

  useEffect(() => {
    const root = document.documentElement;
    if (choice === "system") delete root.dataset.theme;
    else root.dataset.theme = choice;
    try {
      if (choice === "system") localStorage.removeItem(KEY);
      else localStorage.setItem(KEY, choice);
    } catch {
      /* private mode: theme still applies for this visit */
    }
  }, [choice]);

  const isDark = useCallback(() => {
    if (choice !== "system") return choice === "dark";
    return window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false;
  }, [choice]);

  const toggle = useCallback(() => setChoice(isDark() ? "light" : "dark"), [isDark]);
  return { choice, setChoice, toggle, isDark };
}
