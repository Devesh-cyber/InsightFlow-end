import { useEffect, useState, type FormEvent } from "react";
import { Note } from "../components/ui";
import { supabase } from "../lib/supabase";

/** After a password-reset link, Supabase signs the user in and fires PASSWORD_RECOVERY: ask for the new password. */
export function PasswordRecovery() {
  const [open, setOpen] = useState(false);
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const { data } = supabase.auth.onAuthStateChange((event) => {
      if (event === "PASSWORD_RECOVERY") setOpen(true);
    });
    return () => data.subscription.unsubscribe();
  }, []);

  if (!open) return null;

  async function save(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    const { error } = await supabase.auth.updateUser({ password });
    setBusy(false);
    if (error) setError(error.message);
    else setOpen(false);
  }

  return (
    <div className="dialog-backdrop" role="dialog" aria-modal="true" aria-labelledby="pw-title">
      <form className="dialog" onSubmit={save}>
        <h2 id="pw-title" style={{ fontSize: 18 }}>Set a new password</h2>
        <label className="field">New password
          <input className="input" type="password" autoComplete="new-password" minLength={6} autoFocus value={password} onChange={(e) => setPassword(e.target.value)} />
        </label>
        {error && <Note tone="danger">{error}</Note>}
        <div className="row">
          <button className="btn primary" disabled={busy || password.length < 6}>Save password</button>
          <button type="button" className="btn ghost" onClick={() => setOpen(false)}>Later</button>
        </div>
      </form>
    </div>
  );
}
