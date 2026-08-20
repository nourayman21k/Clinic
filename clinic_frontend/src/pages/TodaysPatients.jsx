import { useState, useEffect, useCallback } from "react";
import { apiFetch } from "../api";
import DateStrip from "../components/DateStrip";
import { todayStr } from "../utils/date";

export default function TodaysPatients({ token }) {
  const [selectedDate, setSelectedDate] = useState(() => todayStr());
  const [appointments, setAppointments] = useState([]);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    apiFetch(`/api/doctor/appointments/today?date=${selectedDate}`, token)
      .then((data) => {
        setAppointments(data);
        setError("");
      })
      .catch((e) => setError(e.message));
  }, [token, selectedDate]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="card" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <label style={{ fontSize: 13, fontWeight: 600 }}>
        {selectedDate === todayStr() ? "Today's patients" : `Patients — ${selectedDate}`}
      </label>

      <DateStrip selectedDate={selectedDate} onSelect={setSelectedDate} />

      {error && (
        <div style={{ background: "var(--danger-soft)", color: "var(--danger)", padding: "8px 12px", borderRadius: 8, fontSize: 13 }}>
          {error}
        </div>
      )}

      {appointments.length === 0 && !error && (
        <div style={{ color: "var(--ink-soft)", fontSize: 13 }}>No appointments scheduled for this day.</div>
      )}

      {appointments.map((a) => (
        <div
          key={a.appointment_id}
          style={{ display: "flex", alignItems: "center", gap: 10, padding: "8px 0", borderBottom: "1px solid var(--border)" }}
        >
          <span style={{ fontWeight: 600, minWidth: 60 }}>
            {new Date(a.scheduled_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
          </span>
          <span style={{ flex: 1 }}>{a.patient_name}</span>
          <span style={{ color: "var(--ink-soft)", fontSize: 13 }}>{a.patient_phone}</span>
          <span style={{ color: "var(--ink-soft)", fontSize: 12, textTransform: "capitalize" }}>{a.appointment_type}</span>
          <span className="badge badge-pending">{a.status}</span>
          {a.appointment_type === "consultation" && a.payment_status && a.payment_status !== "paid" && (
            <span className="badge badge-noshow" title="Consultation fee not yet collected">⚠️ Unpaid</span>
          )}
        </div>
      ))}
    </div>
  );
}
