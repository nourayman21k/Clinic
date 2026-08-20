const BASE_URL = "http://127.0.0.1:8001";

export async function login(username, password) {
  const body = new URLSearchParams();
  body.set("username", username);
  body.set("password", password);

  const res = await fetch(`${BASE_URL}/api/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "Login failed");
  }
  return res.json(); // { access_token, token_type, role, full_name }
}

export async function uploadVoiceCharge(token, blob) {
  const formData = new FormData();
  formData.append("audio", blob, "recording.webm");

  const res = await fetch(`${BASE_URL}/api/doctor/voice-charge`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` }, // no Content-Type — browser sets the multipart boundary
    body: formData,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Request failed (${res.status})`);
  }
  return res.json();
}

export async function uploadVoiceTreatmentPlan(token, blob) {
  const formData = new FormData();
  formData.append("audio", blob, "recording.webm");

  const res = await fetch(`${BASE_URL}/api/doctor/voice-treatment-plan`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` },
    body: formData,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Request failed (${res.status})`);
  }
  return res.json();
}

export async function collectPayment(token, paymentId, method, receiptFile) {
  const formData = new FormData();
  formData.append("method", method);
  if (receiptFile) formData.append("receipt", receiptFile);

  const res = await fetch(`${BASE_URL}/api/secretary/payments/${paymentId}/collect`, {
    method: "POST",
    headers: { Authorization: `Bearer ${token}` }, // no Content-Type — browser sets the multipart boundary
    body: formData,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Request failed (${res.status})`);
  }
  return res.json();
}

export function receiptUrl(path) {
  return path ? `${BASE_URL}/uploads/${path}` : null;
}

export async function downloadMonthlyReportPdf(token, year, month) {
  // A plain <a href> can't carry the Authorization header this endpoint
  // requires, so fetch the PDF as a blob and trigger the download manually.
  const res = await fetch(`${BASE_URL}/api/doctor/analytics/monthly/pdf?year=${year}&month=${month}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Request failed (${res.status})`);
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `report_${year}_${String(month).padStart(2, "0")}.pdf`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export async function apiFetch(path, token, options = {}) {
  const res = await fetch(`${BASE_URL}${path}`, {
    ...options,
    headers: {
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      Authorization: `Bearer ${token}`,
      ...(options.headers || {}),
    },
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Request failed (${res.status})`);
  }
  return res.json();
}