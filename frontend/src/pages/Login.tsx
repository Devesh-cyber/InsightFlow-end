import { useState, type FormEvent } from "react";
import { Link, Navigate, useLocation, useSearchParams } from "react-router-dom";
import { useAuth } from "../auth/AuthProvider";
import { Logo, Note } from "../components/ui";
import { supabase } from "../lib/supabase";

type Mode = "signin" | "signup" | "reset";

export default function Login() {
  const { session } = useAuth();
  const location = useLocation();
  const from = (location.state as { from?: string } | null)?.from ?? "/app";
  const [params] = useSearchParams();
  const [mode, setMode] = useState<Mode>(params.get("mode") === "signup" ? "signup" : "signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  if (session) return <Navigate to={from} replace />;

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setInfo(null);
    try {
      if (mode === "signin") {
        const { error } = await supabase.auth.signInWithPassword({ email, password });
        if (error) throw error;
      } else if (mode === "signup") {
        const { data, error } = await supabase.auth.signUp({ email, password, options: { emailRedirectTo: window.location.origin } });
        if (error) throw error;
        if (!data.session) setInfo("Check your inbox to confirm your email, then sign in.");
      } else {
        const { error } = await supabase.auth.resetPasswordForEmail(email, { redirectTo: window.location.origin });
        if (error) throw error;
        setInfo("If that email has an account, a reset link is on its way.");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign-in failed.");
    } finally {
      setBusy(false);
    }
  }

  async function google() {
    setError(null);
    const { error } = await supabase.auth.signInWithOAuth({ provider: "google", options: { redirectTo: window.location.origin } });
    if (error) setError(error.message);
  }

  const titles: Record<Mode, string> = { signin: "Sign in", signup: "Create an account", reset: "Reset your password" };

  return (
    <div className="auth">
      <form className="auth-card" onSubmit={submit} noValidate>
        <Link to="/" className="brand" style={{ padding: 0 }} title="Back to the InsightFlow home page"><Logo size={26} />InsightFlow</Link>
        <div>
          <h1 style={{ fontSize: 22 }}>{titles[mode]}</h1>
          <p className="muted small" style={{ marginTop: 4 }}>Understand your data before you change it.</p>
        </div>

        {mode !== "reset" && (
          <>
            <button type="button" className="btn" onClick={google}>
              <svg width="18" height="18" viewBox="0 0 48 48" aria-hidden="true">
                <path fill="#FFC107" d="M43.6 20.5H42V20H24v8h11.3C33.7 32.7 29.2 36 24 36c-6.6 0-12-5.4-12-12s5.4-12 12-12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 12.9 4 4 12.9 4 24s8.9 20 20 20 20-8.9 20-20c0-1.3-.1-2.4-.4-3.5z" />
                <path fill="#FF3D00" d="m6.3 14.7 6.6 4.8C14.7 15.1 19 12 24 12c3.1 0 5.8 1.2 7.9 3.1l5.7-5.7C34 6.1 29.3 4 24 4 16.3 4 9.7 8.3 6.3 14.7z" />
                <path fill="#4CAF50" d="M24 44c5.2 0 9.9-2 13.4-5.2l-6.2-5.2C29.2 35.1 26.7 36 24 36c-5.2 0-9.6-3.3-11.3-8l-6.5 5C9.5 39.6 16.2 44 24 44z" />
                <path fill="#1976D2" d="M43.6 20.5H42V20H24v8h11.3c-.8 2.2-2.2 4.1-4.1 5.6l6.2 5.2C37 39.2 44 34 44 24c0-1.3-.1-2.4-.4-3.5z" />
              </svg>
              Continue with Google
            </button>
            <div className="divider">or with email</div>
          </>
        )}

        <label className="field">Email
          <input className="input" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
        </label>
        {mode !== "reset" && (
          <label className="field">Password
            <input className="input" type="password" required minLength={6}
              autoComplete={mode === "signup" ? "new-password" : "current-password"} value={password} onChange={(e) => setPassword(e.target.value)} />
          </label>
        )}

        {error && <Note tone="danger">{error}</Note>}
        {info && <Note tone="accent">{info}</Note>}

        <button className="btn primary" type="submit" disabled={busy || !email || (mode !== "reset" && password.length < 6)}>
          {busy && <span className="spinner" />}
          {mode === "signin" ? "Sign in" : mode === "signup" ? "Create account" : "Send reset link"}
        </button>

        <div className="row small" style={{ justifyContent: "space-between" }}>
          {mode === "signin" ? (
            <>
              <button type="button" className="link-btn" onClick={() => setMode("signup")}>Create an account</button>
              <button type="button" className="link-btn" style={{ color: "var(--muted)" }} onClick={() => setMode("reset")}>Forgot password?</button>
            </>
          ) : (
            <button type="button" className="link-btn" onClick={() => setMode("signin")}>{mode === "signup" ? "I already have an account" : "← Back to sign in"}</button>
          )}
        </div>
      </form>
    </div>
  );
}