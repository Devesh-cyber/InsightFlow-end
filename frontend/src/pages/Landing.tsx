import { Link, Navigate } from "react-router-dom";
import { useAuth } from "../auth/AuthProvider";
import { Logo } from "../components/ui";

const STAGES = [
  {
    n: "1", title: "Primary cleaning",
    text: "Wrong types, spelling variants, extra spaces, duplicates, impossible values and totals that don't add up — found for you, with a preview before anything changes.",
  },
  {
    n: "2", title: "Explore & review",
    text: "Charts, column profiles and relationships. Outliers and skew are shown as questions — real or wrong? — so you decide instead of clicking every fix.",
  },
  {
    n: "3", title: "ML-ready dataset",
    text: "Your own rules per column: fill, clip, log, scale, encode, date parts, new features. Split into train and test, then download.",
  },
];

const POINTS = [
  { title: "Nothing changes without a preview", text: "Every step shows the exact rows it will change. Each one is saved as a new version, and the original file is never touched." },
  { title: "Honest about guesses", text: "Filled gaps and capped values are tagged as estimates, in the app and in the report, so nobody mistakes them for real data." },
  { title: "Your rules, not ours", text: "Build if-this-then-that rules and saved checks that re-run on every version. Suggestions are hints, never applied on their own." },
  { title: "Take it with you", text: "Export CSV or Excel, a written report, a pandas script that reproduces every step, and train/test files learned without leakage." },
];

/** Public landing page. Signed-in visitors go straight to their datasets. */
export default function Landing() {
  const { session, loading } = useAuth();
  if (!loading && session) return <Navigate to="/app" replace />;

  return (
    <div className="landing">
      <header className="lp-nav">
        <Link to="/" className="brand" style={{ padding: 0 }}><Logo size={24} />InsightFlow</Link>
        <nav className="row" style={{ gap: 8 }}>
          <a className="btn ghost" href="#how">How it works</a>
          <Link className="btn ghost" to="/login">Log in</Link>
          <Link className="btn primary" to="/login?mode=signup">Get started</Link>
        </nav>
      </header>

      <main>
        <section className="lp-hero">
          <div className="lp-hero-text">
            <span className="lp-eyebrow">For data science and ML students and developers</span>
            <h1>Understand your data before you change it.</h1>
            <p className="lp-lead">
              Upload a CSV or Excel file. InsightFlow finds what's wrong, shows you the exact rows, and walks you from
              messy data to a clean, ML-ready dataset — one previewed, reversible step at a time.
            </p>
            <div className="row" style={{ gap: 10 }}>
              <Link className="btn primary lg" to="/login?mode=signup">Get started — it's free</Link>
              <Link className="btn lg" to="/login">I have an account</Link>
            </div>
          </div>

          <div className="lp-mock" aria-hidden="true">
            <div className="lp-mock-strip">
              <span className="lp-step done"><b>✓</b> Primary cleaning</span>
              <span className="lp-step on"><b>2</b> Explore & review</span>
              <span className="lp-step"><b>3</b> ML-ready</span>
            </div>
            <div className="lp-mock-card">
              <div className="row" style={{ gap: 8 }}>
                <span className="sev warning">Warning</span>
                <span className="strong small">'total_amount' ≠ units × unit_price in 5 rows</span>
              </div>
              <div className="xs muted">In 115 of 120 rows the formula holds. These 5 don't, so one of the three values is probably wrong.</div>
              <div className="lp-mock-rows">
                <span>row 32</span><span className="hl">10,522.93</span><span>0 × 10,522.93</span>
                <span>row 71</span><span className="hl">58,934.85</span><span>5 × 999,999.99</span>
                <span>row 76</span><span className="hl">1.00</span><span>5 × 13,466.35</span>
              </div>
              <div className="row" style={{ gap: 8 }}>
                <span className="option rec">Preview a fix</span>
                <span className="link-btn">Looks fine, keep it</span>
              </div>
            </div>
            <div className="lp-mock-card small-card">
              <span className="sev info">note</span>
              <span className="small">'income' is right-skewed — a log transform can help linear models.</span>
            </div>
          </div>
        </section>

        <section id="how" className="lp-section">
          <h2 className="lp-h2">From messy file to ML-ready, in three stages</h2>
          <p className="lp-sub">The same order you'd follow by hand — so you never fix outliers before the basics are right.</p>
          <div className="lp-stages">
            {STAGES.map((s, i) => (
              <article key={s.n} className="lp-stage">
                <span className="lp-n">{s.n}</span>
                <h3>{s.title}</h3>
                <p>{s.text}</p>
                {i < STAGES.length - 1 && <span className="lp-arrow" aria-hidden="true">→</span>}
              </article>
            ))}
          </div>
        </section>

        <section className="lp-section">
          <h2 className="lp-h2">Why InsightFlow</h2>
          <div className="lp-points">
            {POINTS.map((p) => (
              <article key={p.title} className="lp-point">
                <h3>{p.title}</h3>
                <p>{p.text}</p>
              </article>
            ))}
          </div>
        </section>

        <section className="lp-cta">
          <h2 className="lp-h2">Ready to look at your data properly?</h2>
          <p className="lp-sub">Create a free account, upload a file, and see what InsightFlow finds.</p>
          <div className="row" style={{ gap: 10, justifyContent: "center" }}>
            <Link className="btn primary lg" to="/login?mode=signup">Create an account</Link>
            <Link className="btn lg" to="/login">Log in</Link>
          </div>
        </section>
      </main>

      <footer className="lp-footer">
        <span className="row" style={{ gap: 8 }}><Logo size={18} />InsightFlow</span>
        <span className="xs muted">Your uploaded file is never modified. Every change is a new version you can revert.</span>
      </footer>
    </div>
  );
}