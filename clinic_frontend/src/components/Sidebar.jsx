// Role-agnostic icon rail. Renders whatever navItems it's given
// ({key, label, icon, active, onClick}) — role-specific item lists are
// built by the page component (DoctorView/SecretaryView), not here.
export default function Sidebar({ navItems, onLogout }) {
  return (
    <div
      style={{
        width: "var(--sidebar-width)",
        flexShrink: 0,
        background: "var(--sidebar-bg)",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        padding: "20px 0",
        gap: 4,
        position: "sticky",
        top: 0,
        height: "100vh",
      }}
    >
      <div
        style={{
          width: 36,
          height: 36,
          borderRadius: 10,
          background: "var(--accent)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          fontSize: 18,
          marginBottom: 16,
        }}
        aria-hidden="true"
      >
        🦷
      </div>

      <nav style={{ display: "flex", flexDirection: "column", gap: 6, width: "100%", alignItems: "center" }}>
        {navItems.map((item) => (
          <button
            key={item.key}
            onClick={item.onClick}
            title={item.label}
            style={{
              width: 48,
              height: 48,
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              justifyContent: "center",
              gap: 2,
              borderRadius: 12,
              background: item.active ? "rgba(255,255,255,0.12)" : "transparent",
              color: item.active ? "#FFFFFF" : "var(--sidebar-ink-soft)",
              fontSize: 20,
              fontWeight: 600,
              transition: "background 0.15s ease, color 0.15s ease",
            }}
          >
            <span aria-hidden="true">{item.icon}</span>
          </button>
        ))}
      </nav>

      <div style={{ marginTop: "auto" }}>
        <button
          onClick={onLogout}
          title="Log out"
          style={{
            width: 48,
            height: 48,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            borderRadius: 12,
            background: "transparent",
            color: "var(--sidebar-ink-soft)",
            fontSize: 18,
          }}
        >
          ⏻
        </button>
      </div>
    </div>
  );
}
