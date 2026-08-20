import { useState, useEffect, useRef } from "react";
import { useAuth } from "../auth/AuthContext";
import { apiFetch, uploadVoiceCharge, uploadVoiceTreatmentPlan } from "../api";
import MonthlyAnalytics from "./MonthlyAnalytics";
import TodaysPatients from "./TodaysPatients";
import PatientDirectory from "./PatientDirectory";
import AppShell from "../components/AppShell";

export default function DoctorView() {
  const { user, token, logout } = useAuth();

  const [view, setView] = useState("charges"); // charges | analytics | patients | treatment-plan | history
  const [procedures, setProcedures] = useState([]);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState([]);
  const [selectedPatient, setSelectedPatient] = useState(null);
  const [lines, setLines] = useState([]); // [{procedure_id, name, unit_price, quantity, tooth_area}]
  const [notes, setNotes] = useState("");
  const [posting, setPosting] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const debounceRef = useRef(null);

  // "Complete visit": once the doctor is done charging/planning for a patient who has
  // a still-confirmed appointment today, they mark it done themselves -- explicit
  // action, never inferred from posting a charge or a treatment plan.
  const [todayAppt, setTodayAppt] = useState(null);
  const [completingVisit, setCompletingVisit] = useState(false);

  useEffect(() => {
    if (!selectedPatient) {
      setTodayAppt(null);
      return;
    }
    apiFetch(`/api/doctor/patients/${selectedPatient.id}/today-appointment`, token)
      .then(setTodayAppt)
      .catch(() => setTodayAppt(null));
  }, [selectedPatient, token]);

  function completeVisit() {
    if (!todayAppt) return;
    setCompletingVisit(true);
    apiFetch(`/api/secretary/appointments/${todayAppt.appointment_id}/status`, token, {
      method: "PATCH",
      body: JSON.stringify({ status: "done" }),
    })
      .then(() => {
        setSuccess("Visit marked as done.");
        setTodayAppt(null);
      })
      .catch((e) => setError(e.message))
      .finally(() => setCompletingVisit(false));
  }

  // Voice charge (Agent 2): record -> transcribe + extract -> doctor listens back + reviews the
  // transcript -> Accept (pre-fills the form below) or Retry (discards, records again) -> Post charge
  const [recState, setRecState] = useState("idle"); // idle | recording | processing | review
  const [pendingDraft, setPendingDraft] = useState(null); // draft response, held until Accept/Retry
  const [recordedAudioUrl, setRecordedAudioUrl] = useState(null); // for playback during review
  const [voiceWarnings, setVoiceWarnings] = useState([]);
  const [unmatchedLines, setUnmatchedLines] = useState([]);
  const [ambiguousCandidates, setAmbiguousCandidates] = useState([]);
  const mediaRecorderRef = useRef(null);
  const chunksRef = useRef([]);

  // Voice treatment plan: same record -> transcribe+extract -> review -> Accept/Retry
  // shape as the voice charge above, but independent state and a different draft
  // shape (procedure + tooth_area + is_general, no pricing) and commit endpoint.
  const [tpPatientQuery, setTpPatientQuery] = useState("");
  const [tpPatientResults, setTpPatientResults] = useState([]);
  const [tpSelectedPatient, setTpSelectedPatient] = useState(null);
  const [tpItems, setTpItems] = useState([]); // [{procedure_id, name, tooth_area, is_general, notes}]
  const [tpPosting, setTpPosting] = useState(false);
  const [tpError, setTpError] = useState("");
  const [tpSuccess, setTpSuccess] = useState("");
  const [tpRecState, setTpRecState] = useState("idle"); // idle | recording | processing | review
  const [tpPendingDraft, setTpPendingDraft] = useState(null);
  const [tpRecordedAudioUrl, setTpRecordedAudioUrl] = useState(null);
  const [tpWarnings, setTpWarnings] = useState([]);
  const [tpUnmatchedLines, setTpUnmatchedLines] = useState([]);
  const [tpAmbiguousCandidates, setTpAmbiguousCandidates] = useState([]);
  const tpMediaRecorderRef = useRef(null);
  const tpChunksRef = useRef([]);
  const tpDebounceRef = useRef(null);

  const [tpTodayAppt, setTpTodayAppt] = useState(null);
  const [tpCompletingVisit, setTpCompletingVisit] = useState(false);

  useEffect(() => {
    if (!tpSelectedPatient) {
      setTpTodayAppt(null);
      return;
    }
    apiFetch(`/api/doctor/patients/${tpSelectedPatient.id}/today-appointment`, token)
      .then(setTpTodayAppt)
      .catch(() => setTpTodayAppt(null));
  }, [tpSelectedPatient, token]);

  function tpCompleteVisit() {
    if (!tpTodayAppt) return;
    setTpCompletingVisit(true);
    apiFetch(`/api/secretary/appointments/${tpTodayAppt.appointment_id}/status`, token, {
      method: "PATCH",
      body: JSON.stringify({ status: "done" }),
    })
      .then(() => {
        setTpSuccess("Visit marked as done.");
        setTpTodayAppt(null);
      })
      .catch((e) => setTpError(e.message))
      .finally(() => setTpCompletingVisit(false));
  }

  useEffect(() => {
    if (tpDebounceRef.current) clearTimeout(tpDebounceRef.current);
    if (!tpPatientQuery.trim()) {
      setTpPatientResults([]);
      return;
    }
    tpDebounceRef.current = setTimeout(() => {
      apiFetch(`/api/doctor/patients/search?q=${encodeURIComponent(tpPatientQuery)}`, token)
        .then(setTpPatientResults)
        .catch((e) => setTpError(e.message));
    }, 300);
    return () => clearTimeout(tpDebounceRef.current);
  }, [tpPatientQuery, token]);

  function tpPickPatient(p) {
    setTpSelectedPatient(p);
    setTpPatientQuery("");
    setTpPatientResults([]);
    setTpAmbiguousCandidates([]);
  }

  async function tpStartRecording() {
    setTpError("");
    setTpPendingDraft(null);
    setTpWarnings([]);
    setTpUnmatchedLines([]);
    setTpAmbiguousCandidates([]);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mr = new MediaRecorder(stream);
      tpChunksRef.current = [];
      mr.ondataavailable = (e) => {
        if (e.data.size > 0) tpChunksRef.current.push(e.data);
      };
      mr.onstop = () => {
        stream.getTracks().forEach((t) => t.stop());
        const blob = new Blob(tpChunksRef.current, { type: mr.mimeType || "audio/webm" });
        if (blob.size < 2000) {
          setTpError("Recording too short — please try again.");
          setTpRecState("idle");
          return;
        }
        const url = URL.createObjectURL(blob);
        setTpRecordedAudioUrl(url);
        tpHandleVoiceUpload(blob, url);
      };
      tpMediaRecorderRef.current = mr;
      mr.start();
      setTpRecState("recording");
    } catch {
      setTpError("Microphone permission denied. Enable microphone access in your browser settings.");
    }
  }

  function tpStopRecording() {
    tpMediaRecorderRef.current?.stop();
    setTpRecState("processing");
  }

  async function tpHandleVoiceUpload(blob, audioUrl) {
    try {
      const draft = await uploadVoiceTreatmentPlan(token, blob);
      setTpPendingDraft(draft);
      setTpRecState("review");
    } catch (e) {
      setTpError(e.message);
      URL.revokeObjectURL(audioUrl);
      setTpRecordedAudioUrl(null);
      setTpRecState("idle");
    }
  }

  function tpCloseReview() {
    if (tpRecordedAudioUrl) URL.revokeObjectURL(tpRecordedAudioUrl);
    setTpRecordedAudioUrl(null);
    setTpPendingDraft(null);
    setTpRecState("idle");
  }

  function tpApplyDraft() {
    const draft = tpPendingDraft;
    if (!draft) return;
    const heard = draft.patient_name_heard ? ` ("${draft.patient_name_heard}")` : "";
    const warnings = [];

    if (draft.patient_status === "ok") {
      setTpSelectedPatient(draft.patient);
    } else {
      setTpSelectedPatient(null);
      if (draft.patient_status === "missing") warnings.push("Didn't catch the patient's name — please select manually.");
      if (draft.patient_status === "not_found") warnings.push(`No patient found matching${heard} — please search manually.`);
      if (draft.patient_status === "ambiguous") {
        warnings.push(`Multiple patients match${heard} — pick one below.`);
        setTpAmbiguousCandidates(draft.patient_candidates);
      }
    }

    const matched = draft.lines.filter((l) => l.matched);
    const unmatched = draft.lines.filter((l) => !l.matched);
    setTpItems(
      matched.map((l) => ({
        procedure_id: l.procedure_id,
        name: l.name,
        tooth_area: l.tooth_area || "",
        is_general: l.is_general,
        notes: l.notes || "",
      }))
    );
    setTpUnmatchedLines(unmatched);
    if (draft.lines.length === 0) warnings.push("No procedures recognized — add them manually below.");
    else if (unmatched.length > 0) warnings.push("Couldn't match one or more procedures to the catalogue — add them manually (see below).");

    setTpWarnings(warnings);
    tpCloseReview();
  }

  function tpRetryRecording() {
    tpCloseReview();
    tpStartRecording();
  }

  function tpAddProcedure(proc) {
    setTpItems((prev) => [...prev, { procedure_id: proc.id, name: proc.name, tooth_area: "", is_general: false, notes: "" }]);
  }

  function tpUpdateItem(index, field, value) {
    setTpItems((prev) => prev.map((it, i) => (i === index ? { ...it, [field]: value } : it)));
  }

  function tpRemoveItem(index) {
    setTpItems((prev) => prev.filter((_, i) => i !== index));
  }

  async function tpHandlePost() {
    setTpError("");
    setTpSuccess("");
    if (!tpSelectedPatient) {
      setTpError("Select a patient first.");
      return;
    }
    if (tpItems.length === 0) {
      setTpError("Add at least one treatment item.");
      return;
    }
    setTpPosting(true);
    try {
      const body = {
        patient_id: tpSelectedPatient.id,
        items: tpItems.map((it) => ({
          procedure_id: it.procedure_id,
          tooth_area: it.is_general ? null : it.tooth_area || null,
          notes: it.notes || null,
        })),
      };
      const res = await apiFetch("/api/doctor/treatment-plan", token, {
        method: "POST",
        body: JSON.stringify(body),
      });
      setTpSuccess(res.message);
      setTpSelectedPatient(null);
      setTpItems([]);
      setTpWarnings([]);
      setTpUnmatchedLines([]);
      setTpAmbiguousCandidates([]);
    } catch (e) {
      setTpError(e.message);
    } finally {
      setTpPosting(false);
    }
  }

  useEffect(() => {
    apiFetch("/api/doctor/procedures", token).then(setProcedures).catch((e) => setError(e.message));
  }, [token]);

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    if (!query.trim()) {
      setResults([]);
      return;
    }
    debounceRef.current = setTimeout(() => {
      apiFetch(`/api/doctor/patients/search?q=${encodeURIComponent(query)}`, token)
        .then(setResults)
        .catch((e) => setError(e.message));
    }, 300);
    return () => clearTimeout(debounceRef.current);
  }, [query, token]);

  function pickPatient(p) {
    setSelectedPatient(p);
    setQuery("");
    setResults([]);
    setAmbiguousCandidates([]);
  }

  async function startRecording() {
    setError("");
    setPendingDraft(null);
    setVoiceWarnings([]);
    setUnmatchedLines([]);
    setAmbiguousCandidates([]);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mr = new MediaRecorder(stream);
      chunksRef.current = [];
      mr.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };
      mr.onstop = () => {
        stream.getTracks().forEach((t) => t.stop());
        const blob = new Blob(chunksRef.current, { type: mr.mimeType || "audio/webm" });
        if (blob.size < 2000) {
          setError("Recording too short — please try again.");
          setRecState("idle");
          return;
        }
        const url = URL.createObjectURL(blob);
        setRecordedAudioUrl(url);
        handleVoiceUpload(blob, url);
      };
      mediaRecorderRef.current = mr;
      mr.start();
      setRecState("recording");
    } catch {
      setError("Microphone permission denied. Enable microphone access in your browser settings.");
    }
  }

  function stopRecording() {
    mediaRecorderRef.current?.stop();
    setRecState("processing");
  }

  // Uploads for transcription + extraction, then STOPS — doesn't touch the form yet.
  // The doctor listens to the recording and reads the transcript first, and only Accept
  // (applyDraft) actually pre-fills patient/lines/notes. Retry discards everything.
  async function handleVoiceUpload(blob, audioUrl) {
    try {
      const draft = await uploadVoiceCharge(token, blob);
      setPendingDraft(draft);
      setRecState("review");
    } catch (e) {
      setError(e.message);
      URL.revokeObjectURL(audioUrl);
      setRecordedAudioUrl(null);
      setRecState("idle");
    }
  }

  function closeReview() {
    if (recordedAudioUrl) URL.revokeObjectURL(recordedAudioUrl);
    setRecordedAudioUrl(null);
    setPendingDraft(null);
    setRecState("idle");
  }

  function applyDraft() {
    const draft = pendingDraft;
    if (!draft) return;
    const heard = draft.patient_name_heard ? ` ("${draft.patient_name_heard}")` : "";
    const warnings = [];

    if (draft.patient_status === "ok") {
      setSelectedPatient(draft.patient);
    } else {
      setSelectedPatient(null);
      if (draft.patient_status === "missing") warnings.push("Didn't catch the patient's name — please select manually.");
      if (draft.patient_status === "not_found") warnings.push(`No patient found matching${heard} — please search manually.`);
      if (draft.patient_status === "ambiguous") {
        warnings.push(`Multiple patients match${heard} — pick one below.`);
        setAmbiguousCandidates(draft.patient_candidates);
      }
    }

    const matched = draft.lines.filter((l) => l.matched);
    const unmatched = draft.lines.filter((l) => !l.matched);
    setLines(
      matched.map((l) => ({
        procedure_id: l.procedure_id,
        name: l.name,
        unit_price: l.unit_price,
        quantity: l.quantity,
        tooth_area: l.tooth_area || "",
      }))
    );
    setUnmatchedLines(unmatched);
    if (draft.lines.length === 0) warnings.push("No procedures recognized — add them manually below.");
    else if (unmatched.length > 0) warnings.push("Couldn't match one or more procedures to the catalogue — add them manually (see below).");

    setVoiceWarnings(warnings);
    setNotes((n) => n || (draft.transcript ? `🎙️ ${draft.transcript}` : n));

    closeReview();
  }

  function retryRecording() {
    closeReview();
    startRecording();
  }

  function addProcedure(proc) {
    setLines((prev) => [
      ...prev,
      { procedure_id: proc.id, name: proc.name, unit_price: proc.base_price, quantity: 1, tooth_area: "" },
    ]);
  }

  function updateLine(index, field, value) {
    setLines((prev) => prev.map((l, i) => (i === index ? { ...l, [field]: value } : l)));
  }

  function removeLine(index) {
    setLines((prev) => prev.filter((_, i) => i !== index));
  }

  const total = lines.reduce((sum, l) => sum + l.unit_price * Number(l.quantity || 0), 0);

  async function handlePost() {
    setError("");
    setSuccess("");
    if (!selectedPatient) {
      setError("Select a patient first.");
      return;
    }
    if (lines.length === 0) {
      setError("Add at least one procedure.");
      return;
    }
    setPosting(true);
    try {
      const body = {
        patient_id: selectedPatient.id,
        procedures: lines.map((l) => ({
          procedure_id: l.procedure_id,
          quantity: Number(l.quantity),
          tooth_area: l.tooth_area || null,
        })),
        notes: notes || null,
      };
      const res = await apiFetch("/api/doctor/charges", token, {
        method: "POST",
        body: JSON.stringify(body),
      });
      setSuccess(res.message);
      // reset for the next patient
      setSelectedPatient(null);
      setLines([]);
      setNotes("");
      setVoiceWarnings([]);
      setUnmatchedLines([]);
      setAmbiguousCandidates([]);
    } catch (e) {
      setError(e.message);
    } finally {
      setPosting(false);
    }
  }

  const navItems = [
    { key: "charges", label: "Charges", icon: "💳", active: view === "charges", onClick: () => setView("charges") },
    { key: "treatment-plan", label: "Treatment Plan", icon: "📋", active: view === "treatment-plan", onClick: () => setView("treatment-plan") },
    { key: "patients", label: "Today's Patients", icon: "🦷", active: view === "patients", onClick: () => setView("patients") },
    { key: "history", label: "Patient History", icon: "📖", active: view === "history", onClick: () => setView("history") },
    { key: "analytics", label: "Analytics", icon: "📊", active: view === "analytics", onClick: () => setView("analytics") },
  ];
  const viewTitles = { charges: "Charges", "treatment-plan": "Treatment Plan", patients: "Today's Patients", history: "Patient History", analytics: "Analytics" };

  return (
    <AppShell
      navItems={navItems}
      title={viewTitles[view]}
      userName={user?.full_name}
      userRole={user?.role}
      onLogout={logout}
    >
      <div style={{ paddingTop: 20 }}>
      {view === "analytics" && <MonthlyAnalytics token={token} />}
      {view === "patients" && <TodaysPatients token={token} />}
      {view === "history" && (
        <PatientDirectory
          token={token}
          searchEndpoint="/api/doctor/patients/search"
          detailEndpoint="/api/doctor/patients"
          showOptIn={false}
        />
      )}

      {view === "treatment-plan" && (
      <div className="card" style={{ display: "flex", flexDirection: "column", gap: 16, maxWidth: 640 }}>
        <div>
          <label style={{ fontSize: 13, fontWeight: 600 }}>Record treatment plan</label>
          <div style={{ marginTop: 6 }}>
            {tpRecState === "idle" && (
              <button className="btn-secondary" onClick={tpStartRecording}>🎙️ Record treatment plan</button>
            )}
            {tpRecState === "recording" && (
              <button className="btn-primary" style={{ background: "var(--danger)" }} onClick={tpStopRecording}>
                ⏹ Stop recording
              </button>
            )}
            {tpRecState === "processing" && (
              <button className="btn-secondary" disabled>Transcribing…</button>
            )}
          </div>

          {tpRecState === "review" && tpPendingDraft && (
            <div className="card" style={{ marginTop: 10, padding: 14, display: "flex", flexDirection: "column", gap: 10 }}>
              {tpRecordedAudioUrl && (
                <div>
                  <label style={{ fontSize: 12, fontWeight: 600, color: "var(--ink-soft)" }}>Playback</label>
                  <audio controls src={tpRecordedAudioUrl} style={{ width: "100%", marginTop: 4 }} />
                </div>
              )}
              <div>
                <label style={{ fontSize: 12, fontWeight: 600, color: "var(--ink-soft)" }}>Transcript</label>
                <div style={{ marginTop: 4, fontSize: 14 }}>
                  {tpPendingDraft.transcript ? `"${tpPendingDraft.transcript}"` : <em style={{ color: "var(--ink-soft)" }}>No speech detected.</em>}
                </div>
              </div>
              <div style={{ fontSize: 13, color: "var(--ink-soft)" }}>
                {tpPendingDraft.patient_status === "ok" && `Patient: ${tpPendingDraft.patient.name}`}
                {tpPendingDraft.patient_status === "missing" && "No patient name heard."}
                {tpPendingDraft.patient_status === "not_found" && `Patient "${tpPendingDraft.patient_name_heard}" not found in the system.`}
                {tpPendingDraft.patient_status === "ambiguous" && `${tpPendingDraft.patient_candidates.length} patients match "${tpPendingDraft.patient_name_heard}".`}
                {" · "}
                {tpPendingDraft.lines.length === 0
                  ? "No items recognized."
                  : `${tpPendingDraft.lines.filter((l) => l.matched).length}/${tpPendingDraft.lines.length} item(s) matched.`}
              </div>
              <div style={{ display: "flex", gap: 8 }}>
                <button className="btn-primary" onClick={tpApplyDraft}>✅ Accept</button>
                <button className="btn-secondary" onClick={tpRetryRecording}>🔁 Retry</button>
              </div>
            </div>
          )}

          {tpWarnings.length > 0 && (
            <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 10 }}>
              {tpWarnings.map((w, i) => (
                <div key={i} style={{ background: "var(--pending-soft)", color: "var(--pending)", padding: "8px 12px", borderRadius: 8, fontSize: 13 }}>
                  {w}
                </div>
              ))}
            </div>
          )}
          {tpUnmatchedLines.length > 0 && (
            <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 10 }}>
              {tpUnmatchedLines.map((l, i) => (
                <div key={i} style={{ background: "var(--pending-soft)", color: "var(--pending)", padding: "8px 12px", borderRadius: 8, fontSize: 13 }}>
                  Heard "{l.spoken_name}" — no matching procedure in the catalogue.
                </div>
              ))}
            </div>
          )}
          {tpAmbiguousCandidates.length > 0 && (
            <div className="card" style={{ marginTop: 10, padding: 6 }}>
              {tpAmbiguousCandidates.map((p) => (
                <div
                  key={p.id}
                  onClick={() => tpPickPatient(p)}
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

        <div>
          <label style={{ fontSize: 13, fontWeight: 600 }}>Patient</label>
          {tpSelectedPatient && (
            <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 6, background: "var(--accent-soft)", padding: "8px 12px", borderRadius: 8 }}>
              <span style={{ fontWeight: 600 }}>{tpSelectedPatient.name}</span>
              <span style={{ color: "var(--ink-soft)", fontSize: 13 }}>{tpSelectedPatient.phone}</span>
              <button className="btn-secondary" style={{ marginLeft: "auto", padding: "4px 10px", fontSize: 12 }} onClick={() => setTpSelectedPatient(null)}>
                Change
              </button>
            </div>
          )}
          {tpTodayAppt && (
            <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 8, background: "var(--pending-soft)", padding: "8px 12px", borderRadius: 8 }}>
              <span style={{ fontSize: 13, color: "var(--pending)" }}>
                Has a confirmed {tpTodayAppt.appointment_type} appointment today at{" "}
                {new Date(tpTodayAppt.scheduled_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}.
              </span>
              <button className="btn-primary" style={{ marginLeft: "auto", padding: "4px 10px", fontSize: 12 }} disabled={tpCompletingVisit} onClick={tpCompleteVisit}>
                ✅ Complete visit
              </button>
            </div>
          )}
          {!tpSelectedPatient && (
            <div style={{ position: "relative", marginTop: 6 }}>
              <input
                placeholder="Search by name or phone…"
                value={tpPatientQuery}
                onChange={(e) => setTpPatientQuery(e.target.value)}
              />
              {tpPatientResults.length > 0 && (
                <div className="card" style={{ position: "absolute", top: "100%", left: 0, right: 0, zIndex: 10, marginTop: 4, padding: 6 }}>
                  {tpPatientResults.map((p) => (
                    <div
                      key={p.id}
                      onClick={() => tpPickPatient(p)}
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
          )}
        </div>

        <div>
          <label style={{ fontSize: 13, fontWeight: 600 }}>Add treatment item</label>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 6 }}>
            {procedures.map((proc) => (
              <button
                key={proc.id}
                className="btn-secondary"
                style={{ fontSize: 13 }}
                onClick={() => tpAddProcedure(proc)}
              >
                {proc.name}
              </button>
            ))}
          </div>
        </div>

        {tpItems.length > 0 && (
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {tpItems.map((it, i) => (
              <div key={i} style={{ display: "flex", gap: 8, alignItems: "center", background: "var(--bg)", padding: 10, borderRadius: 8 }}>
                <span style={{ flex: 1, fontWeight: 600 }}>{it.name}</span>
                <label style={{ fontSize: 12, display: "flex", alignItems: "center", gap: 4 }}>
                  <input
                    type="checkbox"
                    checked={it.is_general}
                    onChange={(e) => tpUpdateItem(i, "is_general", e.target.checked)}
                  />
                  General
                </label>
                {!it.is_general && (
                  <input
                    placeholder="tooth area"
                    value={it.tooth_area}
                    onChange={(e) => tpUpdateItem(i, "tooth_area", e.target.value)}
                    style={{ width: 130 }}
                  />
                )}
                <input
                  placeholder="notes (optional)"
                  value={it.notes}
                  onChange={(e) => tpUpdateItem(i, "notes", e.target.value)}
                  style={{ width: 160 }}
                />
                <button className="btn-secondary" style={{ padding: "4px 8px", fontSize: 12 }} onClick={() => tpRemoveItem(i)}>✕</button>
              </div>
            ))}
          </div>
        )}

        {tpError && <div style={{ background: "var(--danger-soft)", color: "var(--danger)", padding: "8px 12px", borderRadius: 8, fontSize: 13 }}>{tpError}</div>}
        {tpSuccess && <div style={{ background: "var(--paid-soft)", color: "var(--paid)", padding: "8px 12px", borderRadius: 8, fontSize: 13 }}>{tpSuccess}</div>}

        <button className="btn-primary" onClick={tpHandlePost} disabled={tpPosting}>
          {tpPosting ? "Saving…" : "Save treatment plan"}
        </button>
      </div>
      )}

      {view === "charges" && (
      <div className="card" style={{ display: "flex", flexDirection: "column", gap: 16, maxWidth: 640 }}>
        <div>
          <label style={{ fontSize: 13, fontWeight: 600 }}>Voice charge</label>
          <div style={{ marginTop: 6 }}>
            {recState === "idle" && (
              <button className="btn-secondary" onClick={startRecording}>🎙️ Record charge</button>
            )}
            {recState === "recording" && (
              <button className="btn-primary" style={{ background: "var(--danger)" }} onClick={stopRecording}>
                ⏹ Stop recording
              </button>
            )}
            {recState === "processing" && (
              <button className="btn-secondary" disabled>Transcribing…</button>
            )}
          </div>

          {recState === "review" && pendingDraft && (
            <div className="card" style={{ marginTop: 10, padding: 14, display: "flex", flexDirection: "column", gap: 10 }}>
              {recordedAudioUrl && (
                <div>
                  <label style={{ fontSize: 12, fontWeight: 600, color: "var(--ink-soft)" }}>Playback</label>
                  <audio controls src={recordedAudioUrl} style={{ width: "100%", marginTop: 4 }} />
                </div>
              )}
              <div>
                <label style={{ fontSize: 12, fontWeight: 600, color: "var(--ink-soft)" }}>Transcript</label>
                <div style={{ marginTop: 4, fontSize: 14 }}>
                  {pendingDraft.transcript ? `"${pendingDraft.transcript}"` : <em style={{ color: "var(--ink-soft)" }}>No speech detected.</em>}
                </div>
              </div>
              <div style={{ fontSize: 13, color: "var(--ink-soft)" }}>
                {pendingDraft.patient_status === "ok" && `Patient: ${pendingDraft.patient.name}`}
                {pendingDraft.patient_status === "missing" && "No patient name heard."}
                {pendingDraft.patient_status === "not_found" && `Patient "${pendingDraft.patient_name_heard}" not found in the system.`}
                {pendingDraft.patient_status === "ambiguous" && `${pendingDraft.patient_candidates.length} patients match "${pendingDraft.patient_name_heard}".`}
                {" · "}
                {pendingDraft.lines.length === 0
                  ? "No procedures recognized."
                  : `${pendingDraft.lines.filter((l) => l.matched).length}/${pendingDraft.lines.length} procedure(s) matched.`}
              </div>
              <div style={{ display: "flex", gap: 8 }}>
                <button className="btn-primary" onClick={applyDraft}>✅ Accept</button>
                <button className="btn-secondary" onClick={retryRecording}>🔁 Retry</button>
              </div>
            </div>
          )}

          {voiceWarnings.length > 0 && (
            <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 10 }}>
              {voiceWarnings.map((w, i) => (
                <div key={i} style={{ background: "var(--pending-soft)", color: "var(--pending)", padding: "8px 12px", borderRadius: 8, fontSize: 13 }}>
                  {w}
                </div>
              ))}
            </div>
          )}
          {unmatchedLines.length > 0 && (
            <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 10 }}>
              {unmatchedLines.map((l, i) => (
                <div key={i} style={{ background: "var(--pending-soft)", color: "var(--pending)", padding: "8px 12px", borderRadius: 8, fontSize: 13 }}>
                  Heard "{l.spoken_name}" × {l.quantity} — no matching procedure in the catalogue.
                </div>
              ))}
            </div>
          )}
          {ambiguousCandidates.length > 0 && (
            <div className="card" style={{ marginTop: 10, padding: 6 }}>
              {ambiguousCandidates.map((p) => (
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

        <div>
          <label style={{ fontSize: 13, fontWeight: 600 }}>Patient</label>
          {selectedPatient && (
            <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 6, background: "var(--accent-soft)", padding: "8px 12px", borderRadius: 8 }}>
              <span style={{ fontWeight: 600 }}>{selectedPatient.name}</span>
              <span style={{ color: "var(--ink-soft)", fontSize: 13 }}>{selectedPatient.phone}</span>
              <button className="btn-secondary" style={{ marginLeft: "auto", padding: "4px 10px", fontSize: 12 }} onClick={() => setSelectedPatient(null)}>
                Change
              </button>
            </div>
          )}
          {todayAppt && (
            <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 8, background: "var(--pending-soft)", padding: "8px 12px", borderRadius: 8 }}>
              <span style={{ fontSize: 13, color: "var(--pending)" }}>
                Has a confirmed {todayAppt.appointment_type} appointment today at{" "}
                {new Date(todayAppt.scheduled_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}.
              </span>
              <button className="btn-primary" style={{ marginLeft: "auto", padding: "4px 10px", fontSize: 12 }} disabled={completingVisit} onClick={completeVisit}>
                ✅ Complete visit
              </button>
            </div>
          )}
          {!selectedPatient && (
            <div style={{ position: "relative", marginTop: 6 }}>
              <input
                placeholder="Search by name or phone…"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
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
          )}
        </div>

        <div>
          <label style={{ fontSize: 13, fontWeight: 600 }}>Add procedure</label>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 6 }}>
            {procedures.map((proc) => (
              <button
                key={proc.id}
                className="btn-secondary"
                style={{ fontSize: 13 }}
                onClick={() => addProcedure(proc)}
              >
                {proc.name} <span className="money" style={{ fontWeight: 500 }}>· {proc.base_price} EGP</span>
              </button>
            ))}
          </div>
        </div>

        {lines.length > 0 && (
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {lines.map((l, i) => (
              <div key={i} style={{ display: "flex", gap: 8, alignItems: "center", background: "var(--bg)", padding: 10, borderRadius: 8 }}>
                <span style={{ flex: 1, fontWeight: 600 }}>{l.name}</span>
                <input
                  type="number"
                  min="1"
                  value={l.quantity}
                  onChange={(e) => updateLine(i, "quantity", e.target.value)}
                  style={{ width: 60 }}
                />
                <input
                  placeholder="tooth area (optional)"
                  value={l.tooth_area}
                  onChange={(e) => updateLine(i, "tooth_area", e.target.value)}
                  style={{ width: 150 }}
                />
                <span className="money">{(l.unit_price * l.quantity).toFixed(0)} EGP</span>
                <button className="btn-secondary" style={{ padding: "4px 8px", fontSize: 12 }} onClick={() => removeLine(i)}>✕</button>
              </div>
            ))}
            <div style={{ textAlign: "right", fontSize: 15 }}>
              Total: <span className="money" style={{ fontSize: 18 }}>{total.toFixed(0)} EGP</span>
            </div>
          </div>
        )}

        <div>
          <label style={{ fontSize: 13, fontWeight: 600 }}>Notes (optional)</label>
          <textarea rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} style={{ marginTop: 6, resize: "vertical" }} />
        </div>

        {error && <div style={{ background: "var(--danger-soft)", color: "var(--danger)", padding: "8px 12px", borderRadius: 8, fontSize: 13 }}>{error}</div>}
        {success && <div style={{ background: "var(--paid-soft)", color: "var(--paid)", padding: "8px 12px", borderRadius: 8, fontSize: 13 }}>{success}</div>}

        <button className="btn-primary" onClick={handlePost} disabled={posting}>
          {posting ? "Posting…" : "Post charge"}
        </button>
      </div>
      )}
      </div>
    </AppShell>
  );
}