import { useState, useEffect, useRef, useCallback } from "react";
import { apiFetch } from "../api";
import StatTile from "../components/StatTile";

const APPT_BADGE = { confirmed: "badge-pending", done: "badge-paid", no_show: "badge-noshow", cancelled: "badge-noshow" };
const PAY_BADGE = { pending: "badge-pending", partial: "badge-pending", paid: "badge-paid" };
const TREATMENT_BADGE = { pending: "badge-pending", in_progress: "badge-pending", completed: "badge-paid" };

export default function PatientDirectory({ token, searchEndpoint = "/api/secretary/patients/search", detailEndpoint = "/api/secretary/patients", showOptIn = true }) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState("");
  const [optInBusy, setOptInBusy] = useState(false);
  const debounceRef = useRef(null);

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    if (!query.trim()) return;
    debounceRef.current = setTimeout(() => {
      apiFetch(`${searchEndpoint}?q=${encodeURIComponent(query)}`, token)
        .then(setResults)
        .catch((e) => setError(e.message));
    }, 300);
    return () => clearTimeout(debounceRef.current);
  }, [query, token, searchEndpoint]);

  function handleQueryChange(value) {
    setQuery(value);
    if (!value.trim()) setResults([]);
  }

  const loadDetail = useCallback((id) => {
    apiFetch(`${detailEndpoint}/${id}`, token)
      .then((d) => {
        setDetail(d);
        setError("");
      })
      .catch((e) => setError(e.message));
  }, [token, detailEndpoint]);

  useEffect(() => {
    if (selectedId) loadDetail(selectedId);
  }, [selectedId, loadDetail]);

  function pickPatient(p) {
    setSelectedId(p.id);
    setQuery("");
    setResults([]);
  }

  function toggleOptIn() {
    setOptInBusy(true);
    apiFetch(`/api/secretary/patients/${selectedId}/whatsapp-opt-in`, token, {
      method: "PATCH",
      body: JSON.stringify({ opt_in: !detail.whatsapp_opt_in }),
    })
      .then(() => loadDetail(selectedId))
      .catch((e) => setError(e.message))
      .finally(() => setOptInBusy(false));
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      <div className="card" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <label style={{ fontSize: 13, fontWeight: 600 }}>Find a patient</label>
        <div style={{ position: "relative" }}>
          <input
            placeholder="Search by name or phone…"
            value={query}
            onChange={(e) => handleQueryChange(e.target.value)}
          />
          {results.length > 0 && (
            <div className="card" style={{ position: "absolute", top: "100%", left: 0, right: 0, zIndex: 10, marginTop: 4, padding: 6 }}>
              {results.map((p) => (
                <div
                  key={p.id}
                  onClick={() => pickPatient(p)}
                  style={{ padding: "8px 10px", borderRadius: 6, cursor: "pointer" }}
                  onMouseEnter={(e) => (e.currentTarget.style.background = "var(--accent-soft)")}
                  onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
                >
                  <div style={{ fontWeight: 600 }}>{p.name}</div>
                  <div style={{ fontSize: 12, color: "var(--ink-soft)" }}>{p.phone}</div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {error && (
        <div style={{ background: "var(--danger-soft)", color: "var(--danger)", padding: "8px 12px", borderRadius: 8, fontSize: 13 }}>
          {error}
        </div>
      )}

      {detail && (
        <>
          <div className="card" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <div>
                <div style={{ fontWeight: 700, fontSize: 16 }}>{detail.name}</div>
                <div style={{ color: "var(--ink-soft)", fontSize: 13 }}>{detail.phone}</div>
              </div>
              {showOptIn && (
                <button className="btn-secondary" disabled={optInBusy} onClick={toggleOptIn}>
                  {detail.whatsapp_opt_in ? "📵 Opt out of WhatsApp" : "✅ Opt back in to WhatsApp"}
                </button>
              )}
            </div>
            <div style={{ display: "flex", gap: 14, flexWrap: "wrap" }}>
              <StatTile label="Total spent" value={`${detail.total_spent} EGP`} accent />
              <StatTile label="Total appointments" value={detail.total_appointments} />
              <StatTile label="No-shows" value={detail.no_show_count} />
              <StatTile
                label="Last completed visit"
                value={detail.last_visit ? new Date(detail.last_visit).toLocaleDateString() : "—"}
              />
            </div>
          </div>

          <div className="card" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <label style={{ fontSize: 13, fontWeight: 600 }}>Treatment plan</label>
            {detail.treatment_items.length === 0 && (
              <div style={{ color: "var(--ink-soft)", fontSize: 13 }}>No treatment plan on file yet.</div>
            )}
            {detail.treatment_items.map((t) => (
              <div key={t.treatment_item_id} style={{ display: "flex", alignItems: "center", gap: 10, padding: "6px 0", borderBottom: "1px solid var(--border)", fontSize: 13 }}>
                <span style={{ flex: 1 }}>
                  {t.procedure_name}{t.tooth_area ? ` — tooth ${t.tooth_area}` : " — general"}
                  {t.notes && <span style={{ color: "var(--ink-soft)" }}> ({t.notes})</span>}
                </span>
                <span className={`badge ${TREATMENT_BADGE[t.status] || "badge-pending"}`}>{t.status}</span>
              </div>
            ))}
          </div>

          <div className="card" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <label style={{ fontSize: 13, fontWeight: 600 }}>Appointment history</label>
            {detail.appointments.length === 0 && (
              <div style={{ color: "var(--ink-soft)", fontSize: 13 }}>No appointments yet.</div>
            )}
            {detail.appointments.map((a) => (
              <div key={a.appointment_id} style={{ display: "flex", alignItems: "center", gap: 10, padding: "6px 0", borderBottom: "1px solid var(--border)", fontSize: 13 }}>
                <span style={{ flex: 1 }}>{new Date(a.scheduled_at).toLocaleString()}</span>
                <span style={{ color: "var(--ink-soft)", textTransform: "capitalize" }}>{a.appointment_type}</span>
                <span className={`badge ${APPT_BADGE[a.status] || "badge-pending"}`}>{a.status}</span>
              </div>
            ))}
          </div>

          <div className="card" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <label style={{ fontSize: 13, fontWeight: 600 }}>Payment history</label>
            {detail.payments.length === 0 && (
              <div style={{ color: "var(--ink-soft)", fontSize: 13 }}>No charges yet.</div>
            )}
            {detail.payments.map((p) => (
              <div key={p.payment_id} style={{ display: "flex", flexDirection: "column", gap: 4, padding: "8px 0", borderBottom: "1px solid var(--border)" }}>
                <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                  <span style={{ flex: 1, fontSize: 13, color: "var(--ink-soft)" }}>
                    {new Date(p.created_at).toLocaleDateString()}
                  </span>
                  <span className={`badge ${PAY_BADGE[p.status] || "badge-pending"}`}>{p.status}</span>
                  <span className="money">{p.final_amount.toFixed(0)} EGP</span>
                </div>
                <div style={{ fontSize: 13, color: "var(--ink-soft)" }}>
                  {p.procedures.map((pp) => `${pp.procedure_name} × ${pp.quantity}`).join(", ")}
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
