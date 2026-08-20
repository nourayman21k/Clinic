import { useState, useEffect, useCallback } from "react";
import { useAuth } from "../auth/AuthContext";
import { apiFetch, collectPayment, receiptUrl } from "../api";
import AppShell from "../components/AppShell";
import StatTile from "../components/StatTile";
import DateStrip from "../components/DateStrip";
import TrendChart from "../components/TrendChart";
import PatientDirectory from "./PatientDirectory";
import { todayStr } from "../utils/date";

const METHOD_LABELS = { cash: "Cash", card: "Card", instapay: "InstaPay", vodafone_cash: "Vodafone Cash" };

function PaymentCard({ payment, token, onChanged, onCollected }) {
  const [adjustOpen, setAdjustOpen] = useState(false);
  const [discount, setDiscount] = useState(payment.discount_amount);
  const [insurance, setInsurance] = useState(payment.insurance_covered_amount);
  const [reason, setReason] = useState("");
  const [adjustBusy, setAdjustBusy] = useState(false);
  const [adjustError, setAdjustError] = useState("");

  const [collectOpen, setCollectOpen] = useState(false);
  const [method, setMethod] = useState("cash");
  const [receiptFile, setReceiptFile] = useState(null);
  const [receiptPreview, setReceiptPreview] = useState(null);
  const [collectBusy, setCollectBusy] = useState(false);
  const [collectError, setCollectError] = useState("");

  async function applyAdjust() {
    setAdjustError("");
    setAdjustBusy(true);
    try {
      await apiFetch(`/api/secretary/payments/${payment.payment_id}/adjust`, token, {
        method: "POST",
        body: JSON.stringify({
          discount_amount: Number(discount),
          insurance_covered_amount: Number(insurance),
          reason: reason || null,
        }),
      });
      setAdjustOpen(false);
      setReason("");
      onChanged();
    } catch (e) {
      setAdjustError(e.message);
    } finally {
      setAdjustBusy(false);
    }
  }

  function pickReceipt(e) {
    const file = e.target.files?.[0] || null;
    setReceiptFile(file);
    setReceiptPreview(file ? URL.createObjectURL(file) : null);
  }

  async function confirmCollect() {
    setCollectError("");
    setCollectBusy(true);
    try {
      const result = await collectPayment(token, payment.payment_id, method, receiptFile);
      if (receiptPreview) URL.revokeObjectURL(receiptPreview);
      onCollected(payment, result);
      onChanged();
    } catch (e) {
      setCollectError(e.message);
    } finally {
      setCollectBusy(false);
    }
  }

  const canConfirm = method === "cash" || !!receiptFile;

  return (
    <div id={`payment-${payment.payment_id}`} className="card" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div>
          <span style={{ fontWeight: 700 }}>{payment.patient_name}</span>{" "}
          <span style={{ color: "var(--ink-soft)", fontSize: 13 }}>{payment.patient_phone}</span>
        </div>
        <span className="badge badge-pending">{payment.status}</span>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        {payment.procedures.map((p, i) => (
          <div key={i} style={{ display: "flex", justifyContent: "space-between", fontSize: 13 }}>
            <span>
              {p.procedure_name} × {p.quantity}
              {p.tooth_area ? ` (${p.tooth_area})` : ""}
            </span>
            <span className="money">{p.price_charged.toFixed(0)} EGP</span>
          </div>
        ))}
      </div>

      {payment.notes && <div style={{ fontSize: 13, color: "var(--ink-soft)" }}>📝 {payment.notes}</div>}

      <div style={{ fontSize: 13, display: "flex", flexDirection: "column", gap: 2, background: "var(--bg)", padding: 10, borderRadius: 8 }}>
        <div style={{ display: "flex", justifyContent: "space-between" }}>
          <span>Base</span>
          <span className="money">{payment.base_amount.toFixed(0)} EGP</span>
        </div>
        {payment.discount_amount > 0 && (
          <div style={{ display: "flex", justifyContent: "space-between", color: "var(--danger)" }}>
            <span>Discount</span>
            <span className="money">-{payment.discount_amount.toFixed(0)} EGP</span>
          </div>
        )}
        {payment.insurance_covered_amount > 0 && (
          <div style={{ display: "flex", justifyContent: "space-between", color: "var(--danger)" }}>
            <span>Insurance</span>
            <span className="money">-{payment.insurance_covered_amount.toFixed(0)} EGP</span>
          </div>
        )}
        <div style={{ display: "flex", justifyContent: "space-between", fontWeight: 700 }}>
          <span>Final</span>
          <span className="money">{payment.final_amount.toFixed(0)} EGP</span>
        </div>
      </div>

      <div style={{ display: "flex", gap: 8 }}>
        <button className="btn-secondary" onClick={() => setAdjustOpen((v) => !v)}>
          {adjustOpen ? "Cancel" : "Apply discount / insurance"}
        </button>
        <button className="btn-secondary" onClick={() => setCollectOpen((v) => !v)}>
          {collectOpen ? "Cancel" : "Collect payment"}
        </button>
      </div>

      {adjustOpen && (
        <div style={{ display: "flex", flexDirection: "column", gap: 8, borderTop: "1px solid var(--border)", paddingTop: 10 }}>
          <div style={{ display: "flex", gap: 8 }}>
            <label style={{ flex: 1, fontSize: 12, fontWeight: 600 }}>
              Discount (EGP)
              <input type="number" min="0" value={discount} onChange={(e) => setDiscount(e.target.value)} style={{ marginTop: 4 }} />
            </label>
            <label style={{ flex: 1, fontSize: 12, fontWeight: 600 }}>
              Insurance covered (EGP)
              <input type="number" min="0" value={insurance} onChange={(e) => setInsurance(e.target.value)} style={{ marginTop: 4 }} />
            </label>
          </div>
          <label style={{ fontSize: 12, fontWeight: 600 }}>
            Reason / insurance provider
            <textarea
              rows={2}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="e.g. Allianz — covers 50%"
              style={{ marginTop: 4, resize: "vertical" }}
            />
          </label>
          {adjustError && (
            <div style={{ background: "var(--danger-soft)", color: "var(--danger)", padding: "8px 12px", borderRadius: 8, fontSize: 13 }}>
              {adjustError}
            </div>
          )}
          <button className="btn-primary" onClick={applyAdjust} disabled={adjustBusy}>
            {adjustBusy ? "Applying…" : "Apply"}
          </button>
        </div>
      )}

      {collectOpen && (
        <div style={{ display: "flex", flexDirection: "column", gap: 8, borderTop: "1px solid var(--border)", paddingTop: 10 }}>
          <label style={{ fontSize: 12, fontWeight: 600 }}>
            Payment method
            <select
              value={method}
              onChange={(e) => {
                setMethod(e.target.value);
                setReceiptFile(null);
                setReceiptPreview(null);
              }}
              style={{ marginTop: 4 }}
            >
              {Object.entries(METHOD_LABELS).map(([value, label]) => (
                <option key={value} value={value}>{label}</option>
              ))}
            </select>
          </label>
          {method !== "cash" && (
            <label style={{ fontSize: 12, fontWeight: 600 }}>
              Receipt photo (required)
              <input type="file" accept="image/*" onChange={pickReceipt} style={{ marginTop: 4 }} />
            </label>
          )}
          {receiptPreview && (
            <img src={receiptPreview} alt="Receipt preview" style={{ maxWidth: "100%", borderRadius: 8, border: "1px solid var(--border)" }} />
          )}
          {collectError && (
            <div style={{ background: "var(--danger-soft)", color: "var(--danger)", padding: "8px 12px", borderRadius: 8, fontSize: 13 }}>
              {collectError}
            </div>
          )}
          <button className="btn-primary" onClick={confirmCollect} disabled={collectBusy || !canConfirm}>
            {collectBusy ? "Collecting…" : "Confirm collect"}
          </button>
        </div>
      )}
    </div>
  );
}

export default function SecretaryView() {
  const { user, token, logout } = useAuth();

  const [payments, setPayments] = useState([]);
  const [loadError, setLoadError] = useState("");
  const [recentlyCollected, setRecentlyCollected] = useState([]);

  const [revenueDate, setRevenueDate] = useState(() => todayStr());
  const [revenue, setRevenue] = useState(null);
  const [revenueError, setRevenueError] = useState("");

  const [revenueTrend, setRevenueTrend] = useState([]);
  const [trendError, setTrendError] = useState("");

  const [todaysAppts, setTodaysAppts] = useState([]);
  const [apptsError, setApptsError] = useState("");

  const [transactions, setTransactions] = useState({ transactions: [], total: 0 });
  const [txError, setTxError] = useState("");

  const loadPending = useCallback(() => {
    apiFetch("/api/secretary/pending", token).then(setPayments).catch((e) => setLoadError(e.message));
  }, [token]);

  // Follows the same date picker as revenue/transactions — depending on
  // revenueDate here means the mount effect below re-runs on every day change,
  // so all three cards always show the same selected day.
  const loadAppointments = useCallback(() => {
    apiFetch(`/api/secretary/appointments/today?date=${revenueDate}`, token).then(setTodaysAppts).catch((e) => setApptsError(e.message));
  }, [token, revenueDate]);

  useEffect(() => {
    loadPending();
    loadAppointments();
  }, [loadPending, loadAppointments]);

  // Rolling 14-day trend, independent of the day picker above.
  useEffect(() => {
    apiFetch("/api/secretary/revenue-trend?days=14", token)
      .then((d) => {
        setRevenueTrend(d);
        setTrendError("");
      })
      .catch((e) => setTrendError(e.message));
  }, [token]);

  // Revenue summary and the itemized transaction list both follow the same
  // date picker — kept in one effect so they always show the same day.
  useEffect(() => {
    apiFetch(`/api/secretary/daily-total?date=${revenueDate}`, token)
      .then((d) => {
        setRevenue(d);
        setRevenueError("");
      })
      .catch((e) => setRevenueError(e.message));
    apiFetch(`/api/secretary/transactions?date=${revenueDate}`, token)
      .then((d) => {
        setTransactions(d);
        setTxError("");
      })
      .catch((e) => setTxError(e.message));
  }, [revenueDate, token]);

  function handleCollected(payment, result) {
    setRecentlyCollected((prev) => [
      { ...result, patient_name: payment.patient_name },
      ...prev,
    ].slice(0, 5));
    // the collected day may or may not be the one currently shown — refresh if it is
    if (revenueDate === todayStr()) {
      apiFetch(`/api/secretary/daily-total?date=${revenueDate}`, token).then(setRevenue).catch(() => {});
      apiFetch(`/api/secretary/transactions?date=${revenueDate}`, token).then(setTransactions).catch(() => {});
    }
    loadAppointments();
  }

  const [statusError, setStatusError] = useState("");
  const [statusBusyId, setStatusBusyId] = useState(null);

  function updateAppointmentStatus(appointmentId, status) {
    setStatusError("");
    setStatusBusyId(appointmentId);
    apiFetch(`/api/secretary/appointments/${appointmentId}/status`, token, {
      method: "PATCH",
      body: JSON.stringify({ status }),
    })
      .then(() => loadAppointments())
      .catch((e) => setStatusError(e.message))
      .finally(() => setStatusBusyId(null));
  }

  const [rescheduleOpenId, setRescheduleOpenId] = useState(null);
  const [newDate, setNewDate] = useState("");
  const [newTime, setNewTime] = useState("");
  const [rescheduleBusy, setRescheduleBusy] = useState(false);

  function openReschedule(appointmentId) {
    setStatusError("");
    setRescheduleOpenId(appointmentId);
    setNewDate("");
    setNewTime("");
  }

  function submitReschedule(appointmentId) {
    setStatusError("");
    setRescheduleBusy(true);
    apiFetch(`/api/secretary/appointments/${appointmentId}/reschedule`, token, {
      method: "PATCH",
      body: JSON.stringify({ new_date: newDate, new_time: newTime }),
    })
      .then(() => {
        setRescheduleOpenId(null);
        loadAppointments();
      })
      .catch((e) => setStatusError(e.message))
      .finally(() => setRescheduleBusy(false));
  }

  function scrollToPatientPayment(patientId) {
    const match = payments.find((p) => p.patient_id === patientId);
    if (!match) return;
    const el = document.getElementById(`payment-${match.payment_id}`);
    if (!el) return;
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    el.style.transition = "background-color 0.3s";
    el.style.backgroundColor = "var(--accent-soft)";
    setTimeout(() => {
      el.style.backgroundColor = "";
    }, 1500);
  }

  // Sidebar nav here is scroll-anchor, not conditional-mount tabs: the whole
  // page stays mounted at all times, so a PaymentCard's open adjust/collect
  // panel (unsaved discount, picked receipt file) never gets discarded by
  // switching sections -- it just scrolls into view.
  const [view, setView] = useState("overview"); // overview (scroll-anchor sections) | patients

  function scrollToSection(id) {
    document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function goToOverviewSection(id) {
    // Switching FROM the patients view means the overview sections aren't
    // mounted yet -- defer the scroll until after the re-render commits.
    setView("overview");
    setTimeout(() => scrollToSection(id), 50);
  }

  const navItems = [
    { key: "overview", label: "Overview", icon: "🏠", active: view === "overview", onClick: () => goToOverviewSection("sec-overview") },
    { key: "payments", label: "Payments", icon: "💳", active: false, onClick: () => goToOverviewSection("sec-payments") },
    { key: "transactions", label: "Transactions", icon: "🧾", active: false, onClick: () => goToOverviewSection("sec-transactions") },
    { key: "patients", label: "Patients", icon: "🧑‍🤝‍🧑", active: view === "patients", onClick: () => setView("patients") },
  ];

  return (
    <AppShell
      navItems={navItems}
      title={view === "patients" ? "Patients" : "Overview"}
      userName={user?.full_name}
      userRole={user?.role}
      onLogout={logout}
    >
      <div style={{ paddingTop: 20, display: "flex", flexDirection: "column", gap: 20 }}>

      {view === "patients" && <PatientDirectory token={token} />}

      {view === "overview" && (
      <>
      <section id="sec-overview" style={{ display: "flex", flexDirection: "column", gap: 16 }}>
        <DateStrip selectedDate={revenueDate} onSelect={setRevenueDate} />

        {revenueError && (
          <div style={{ background: "var(--danger-soft)", color: "var(--danger)", padding: "8px 12px", borderRadius: 8, fontSize: 13 }}>
            {revenueError}
          </div>
        )}
        {revenue && (
          <div style={{ display: "flex", gap: 14, flexWrap: "wrap" }}>
            <StatTile label={`Collected — ${revenue.day_of_week}`} value={`${revenue.net_collected.toFixed(0)} EGP`} accent />
            <StatTile label="Payments" value={revenue.payments_collected} />
            <StatTile label="Gross billed" value={`${revenue.gross_billed.toFixed(0)} EGP`} />
            <StatTile label="Discounts given" value={`${revenue.total_discounts_given.toFixed(0)} EGP`} />
            <StatTile label="Insurance covered" value={`${revenue.total_insurance_covered.toFixed(0)} EGP`} />
          </div>
        )}

        <div className="card">
          <label style={{ fontSize: 13, fontWeight: 600 }}>Revenue — last 14 days</label>
          {trendError && (
            <div style={{ background: "var(--danger-soft)", color: "var(--danger)", padding: "8px 12px", borderRadius: 8, fontSize: 13, marginTop: 8 }}>
              {trendError}
            </div>
          )}
          {revenueTrend.length > 0 && (
            <TrendChart data={revenueTrend} dataKey="revenue" formatValue={(v) => `${v.toFixed(0)} EGP`} />
          )}
        </div>

        <div className="card" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <label style={{ fontSize: 13, fontWeight: 600 }}>
            {revenueDate === todayStr() ? "Today's appointments" : `Appointments — ${revenueDate}`}
          </label>
          {apptsError && (
            <div style={{ background: "var(--danger-soft)", color: "var(--danger)", padding: "8px 12px", borderRadius: 8, fontSize: 13 }}>
              {apptsError}
            </div>
          )}
          {todaysAppts.length === 0 && !apptsError && (
            <div style={{ color: "var(--ink-soft)", fontSize: 13 }}>No appointments scheduled for this day.</div>
          )}
          {statusError && (
            <div style={{ background: "var(--danger-soft)", color: "var(--danger)", padding: "8px 12px", borderRadius: 8, fontSize: 13 }}>
              {statusError}
            </div>
          )}
          {todaysAppts.map((a) => (
            <div key={a.appointment_id} style={{ borderBottom: "1px solid var(--border)" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "8px 0" }}>
                <span style={{ fontWeight: 600, minWidth: 60 }}>
                  {new Date(a.scheduled_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                </span>
                <span style={{ flex: 1 }}>{a.patient_name}</span>
                <span style={{ color: "var(--ink-soft)", fontSize: 13 }}>{a.patient_phone}</span>
                <span style={{ color: "var(--ink-soft)", fontSize: 12, textTransform: "capitalize" }}>{a.appointment_type}</span>
                <span className={`badge ${a.status === "no_show" ? "badge-noshow" : a.status === "done" ? "badge-paid" : "badge-pending"}`}>
                  {a.status}
                </span>
                {a.appointment_type === "consultation" && a.payment_status && a.payment_status !== "paid" && (
                  <span className="badge badge-noshow" title="Consultation fee not yet collected">⚠️ Unpaid consultation</span>
                )}
                {a.has_pending_payment_today && (
                  <button className="btn-secondary" style={{ padding: "4px 10px", fontSize: 12 }} onClick={() => scrollToPatientPayment(a.patient_id)}>
                    Unpaid — collect
                  </button>
                )}
                {a.status === "confirmed" && (
                  <>
                    <button
                      className="btn-secondary"
                      style={{ padding: "4px 10px", fontSize: 12 }}
                      disabled={statusBusyId === a.appointment_id}
                      onClick={() => (rescheduleOpenId === a.appointment_id ? setRescheduleOpenId(null) : openReschedule(a.appointment_id))}
                    >
                      {rescheduleOpenId === a.appointment_id ? "Cancel edit" : "Reschedule"}
                    </button>
                    <button
                      className="btn-secondary"
                      style={{ padding: "4px 10px", fontSize: 12 }}
                      disabled={statusBusyId === a.appointment_id}
                      onClick={() => updateAppointmentStatus(a.appointment_id, "done")}
                    >
                      Mark done
                    </button>
                    <button
                      className="btn-secondary"
                      style={{ padding: "4px 10px", fontSize: 12, color: "var(--danger)" }}
                      disabled={statusBusyId === a.appointment_id}
                      onClick={() => updateAppointmentStatus(a.appointment_id, "no_show")}
                    >
                      No-show
                    </button>
                    <button
                      className="btn-secondary"
                      style={{ padding: "4px 10px", fontSize: 12, color: "var(--danger)" }}
                      disabled={statusBusyId === a.appointment_id}
                      onClick={() => updateAppointmentStatus(a.appointment_id, "cancelled")}
                    >
                      Cancel
                    </button>
                  </>
                )}
              </div>
              {rescheduleOpenId === a.appointment_id && (
                <div style={{ display: "flex", gap: 8, alignItems: "flex-end", paddingBottom: 10 }}>
                  <label style={{ fontSize: 12, fontWeight: 600 }}>
                    New date
                    <input type="date" value={newDate} onChange={(e) => setNewDate(e.target.value)} style={{ marginTop: 4, width: 150 }} />
                  </label>
                  <label style={{ fontSize: 12, fontWeight: 600 }}>
                    New time
                    <input type="time" value={newTime} onChange={(e) => setNewTime(e.target.value)} style={{ marginTop: 4, width: 110 }} />
                  </label>
                  <button
                    className="btn-primary"
                    style={{ padding: "8px 14px", fontSize: 13 }}
                    disabled={rescheduleBusy || !newDate || !newTime}
                    onClick={() => submitReschedule(a.appointment_id)}
                  >
                    {rescheduleBusy ? "Saving…" : "Confirm"}
                  </button>
                </div>
              )}
            </div>
          ))}
        </div>
      </section>

      <section id="sec-payments" style={{ display: "flex", flexDirection: "column", gap: 14 }}>
        <label style={{ fontSize: 13, fontWeight: 600 }}>Pending payments</label>
        {loadError && (
          <div style={{ background: "var(--danger-soft)", color: "var(--danger)", padding: "8px 12px", borderRadius: 8, fontSize: 13 }}>
            {loadError}
          </div>
        )}
        {payments.length === 0 && !loadError && <div className="card" style={{ color: "var(--ink-soft)" }}>No pending payments.</div>}
        {payments.map((p) => (
          <PaymentCard key={p.payment_id} payment={p} token={token} onChanged={loadPending} onCollected={handleCollected} />
        ))}
      </section>

      <section id="sec-transactions" style={{ display: "flex", flexDirection: "column", gap: 20 }}>
        <div className="card" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <label style={{ fontSize: 13, fontWeight: 600 }}>Today's transactions</label>
          {txError && (
            <div style={{ background: "var(--danger-soft)", color: "var(--danger)", padding: "8px 12px", borderRadius: 8, fontSize: 13 }}>
              {txError}
            </div>
          )}
          {transactions.transactions.length === 0 && !txError && (
            <div style={{ color: "var(--ink-soft)", fontSize: 13 }}>No transactions collected today yet.</div>
          )}
          {transactions.transactions.length > 0 && (
            <>
              {transactions.transactions.map((t) => (
                <div key={t.payment_id} style={{ display: "flex", alignItems: "center", gap: 10, padding: "6px 0", borderBottom: "1px solid var(--border)", fontSize: 13 }}>
                  <span style={{ flex: 1 }}>{t.patient_name}</span>
                  <span style={{ color: "var(--ink-soft)" }}>{METHOD_LABELS[t.method] || t.method}</span>
                  <span style={{ color: "var(--ink-soft)" }}>
                    {t.paid_at ? new Date(t.paid_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : ""}
                  </span>
                  <span className="money">{t.final_amount.toFixed(0)} EGP</span>
                </div>
              ))}
              <div style={{ display: "flex", justifyContent: "space-between", paddingTop: 8, fontWeight: 700 }}>
                <span>Total</span>
                <span className="money" style={{ fontSize: 16 }}>{transactions.total.toFixed(0)} EGP</span>
              </div>
            </>
          )}
        </div>

      {recentlyCollected.length > 0 && (
        <div>
          <label style={{ fontSize: 13, fontWeight: 600 }}>Just collected</label>
          <div style={{ display: "flex", flexDirection: "column", gap: 10, marginTop: 8 }}>
            {recentlyCollected.map((c, i) => (
              <div key={i} className="card" style={{ display: "flex", alignItems: "center", gap: 12 }}>
                {receiptUrl(c.receipt_path) && (
                  <img src={receiptUrl(c.receipt_path)} alt="Receipt" style={{ width: 48, height: 48, objectFit: "cover", borderRadius: 6, border: "1px solid var(--border)" }} />
                )}
                <div style={{ flex: 1 }}>
                  <div style={{ fontWeight: 600 }}>{c.patient_name}</div>
                  <div style={{ fontSize: 12, color: "var(--ink-soft)" }}>{METHOD_LABELS[c.method] || c.method}</div>
                </div>
                <span className="money badge badge-paid">{Number(c.final_amount).toFixed(0)} EGP</span>
              </div>
            ))}
          </div>
        </div>
      )}
      </section>
      </>
      )}

      </div>
    </AppShell>
  );
}
