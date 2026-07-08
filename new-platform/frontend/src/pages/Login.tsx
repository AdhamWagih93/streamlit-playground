import { useState } from "react";

import { apiPost } from "../lib/api";
import { useAuth } from "../lib/auth";

export default function Login() {
  const { refresh } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const submitLdap = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr("");
    try {
      await apiPost("/auth/login", { username, password });
      await refresh();
    } catch {
      setErr("Sign-in failed — check your credentials.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ minHeight: "100vh", display: "grid", placeItems: "center" }}>
      <div className="card gold reveal" style={{ width: 380, display: "flex", flexDirection: "column", gap: 14 }}>
        <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
          <svg viewBox="0 0 32 32" width="34" height="34">
            <rect width="32" height="32" rx="7" fill="var(--gold)" />
            <circle cx="16" cy="16" r="9" fill="none" stroke="#0B1020" strokeWidth="2" />
            <path d="M16 7v18M8.5 12h15M8.5 20h15" stroke="#0B1020" strokeWidth="2" fill="none" />
          </svg>
          <div>
            <h2 style={{ letterSpacing: ".08em" }}>MERIDIAN</h2>
            <div className="card-kicker">Engineering Platform</div>
          </div>
        </div>

        <a className="btn primary" href="/api/auth/entra/login" style={{ justifyContent: "center" }}>
          Sign in with Microsoft Entra ID
        </a>

        <div className="card-kicker" style={{ textAlign: "center" }}>
          or directory credentials
        </div>
        <form onSubmit={submitLdap} style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <input className="input" placeholder="Username" value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" />
          <input className="input" placeholder="Password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" />
          {err && <div style={{ color: "var(--err)", fontSize: 12.5 }}>{err}</div>}
          <button className="btn" type="submit" disabled={busy || !username || !password} style={{ justifyContent: "center" }}>
            {busy ? "Signing in…" : "Sign in"}
          </button>
        </form>
        <div className="card-kicker" style={{ textAlign: "center" }}>
          GOVERNED · RBAC-SCOPED · AUDITED
        </div>
      </div>
    </div>
  );
}
