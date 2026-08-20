import Sidebar from "./Sidebar";
import TopBar from "./TopBar";

// Plain prop-driven layout wrapper -- NEVER a router <Route> layout element.
// It owns no tab/view state itself; DoctorView/SecretaryView keep their own
// state exactly as before and just hand this component navItems + children.
export default function AppShell({ navItems, title, userName, userRole, onLogout, children }) {
  return (
    <div style={{ display: "flex", minHeight: "100vh", background: "var(--bg)" }}>
      <Sidebar navItems={navItems} onLogout={onLogout} />
      <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column" }}>
        <TopBar title={title} userName={userName} userRole={userRole} />
        <div style={{ flex: 1, padding: "0 28px 28px", maxWidth: 1100, width: "100%" }}>
          {children}
        </div>
      </div>
    </div>
  );
}
