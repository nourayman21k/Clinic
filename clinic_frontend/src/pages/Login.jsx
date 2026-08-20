import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";

export default function Login() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const { login } = useAuth();
  const navigate = useNavigate();

  async function handleSubmit(e) {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      const data = await login(username, password);
      navigate(data.role === "doctor" ? "/doctor" : "/secretary");
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center", background: "var(--bg)" }}>
      <form onSubmit={handleSubmit} className="card" style={{ width: 360, display: "flex", flexDirection: "column", gap: 16 }}>
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 10, marginBottom: 4 }}>
          <div
            style={{
              width: 48,
              height: 48,
              borderRadius: 14,
              background: "var(--accent)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              fontSize: 24,
            }}
            aria-hidden="true"
          >
            🦷
          </div>
          <div style={{ textAlign: "center" }}>
            <h1 style={{ fontSize: 22, margin: 0 }}>Clinic Staff Login</h1>
            <p style={{ color: "var(--ink-soft)", fontSize: 14, margin: "4px 0 0" }}>Doctor and secretary access</p>
          </div>
        </div>

        <label style={{ fontSize: 13, fontWeight: 600 }}>
          Username
          <input value={username} onChange={(e) => setUsername(e.target.value)} autoFocus style={{ marginTop: 6 }} />
        </label>

        <label style={{ fontSize: 13, fontWeight: 600 }}>
          Password
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} style={{ marginTop: 6 }} />
        </label>

        {error && (
          <div style={{ background: "var(--danger-soft)", color: "var(--danger)", padding: "8px 12px", borderRadius: 8, fontSize: 13 }}>
            {error}
          </div>
        )}

        <button type="submit" className="btn-primary" disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </div>
  );
}