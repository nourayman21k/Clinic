// "Hello, {name}" greeting + role + a decorative (non-wired) search box.
// Existing patient-search inside DoctorView's Charges tab stays exactly
// where it is today — this search box is visual only, not a new global
// search feature.
export default function TopBar({ title, userName, userRole }) {
  const initial = (userName || "?").trim().charAt(0).toUpperCase();
  const roleLabel = userRole === "doctor" ? "Doctor" : userRole === "secretary" ? "Secretary" : "";

  return (
    <div
      style={{
        height: "var(--topbar-height)",
        flexShrink: 0,
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        gap: 16,
        padding: "0 28px",
      }}
    >
      <div>
        <h1 style={{ fontSize: 20, margin: 0 }}>{title}</h1>
      </div>

      <div style={{ flex: 1, maxWidth: 360 }}>
        <input
          placeholder="Search…"
          disabled
          style={{ background: "var(--surface)", opacity: 0.7, cursor: "not-allowed" }}
        />
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <div style={{ textAlign: "right" }}>
          <div style={{ fontSize: 13, color: "var(--ink-soft)" }}>Hello,</div>
          <div style={{ fontSize: 14, fontWeight: 700 }}>{userName}</div>
        </div>
        <div
          style={{
            width: 40,
            height: 40,
            borderRadius: "50%",
            background: "var(--accent-soft)",
            color: "var(--accent)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontWeight: 700,
            fontSize: 15,
          }}
          title={roleLabel}
        >
          {initial}
        </div>
      </div>
    </div>
  );
}
