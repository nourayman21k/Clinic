// Big number + label. Proportional figures at display size (not .money's
// tabular-nums, which is correct for aligned columns but wrong for a single
// standalone hero number) -- see .stat-value in styles.css.
export default function StatTile({ label, value, accent = false }) {
  return (
    <div
      className="card"
      style={{
        flex: "1 1 160px",
        minWidth: 150,
        display: "flex",
        flexDirection: "column",
        gap: 6,
        ...(accent ? { background: "var(--accent)", color: "#FFFFFF", borderColor: "var(--accent)" } : {}),
      }}
    >
      <span style={{ fontSize: 13, color: accent ? "rgba(255,255,255,0.85)" : "var(--ink-soft)" }}>{label}</span>
      <span className="stat-value" style={accent ? { color: "#FFFFFF" } : undefined}>{value}</span>
    </div>
  );
}
