// Horizontal day picker.
import { shiftDateStr, todayStr } from "../utils/date";

const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

export default function DateStrip({ selectedDate, onSelect, daysBefore = 3, daysAfter = 3 }) {
  const [year, month, day] = selectedDate.split("-").map(Number);
  const centerUtc = new Date(Date.UTC(year, month - 1, day));
  const today = todayStr();

  const days = [];
  for (let n = -daysBefore; n <= daysAfter; n++) {
    const d = new Date(centerUtc);
    d.setUTCDate(d.getUTCDate() + n);
    days.push({ str: d.toISOString().slice(0, 10), dayOfMonth: d.getUTCDate(), dow: d.getUTCDay() });
  }

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
      <button className="btn-secondary" onClick={() => onSelect(shiftDateStr(selectedDate, -1))} aria-label="Previous day">
        ←
      </button>

      <div style={{ display: "flex", gap: 6, overflowX: "auto" }}>
        {days.map(({ str, dayOfMonth, dow }) => {
          const active = str === selectedDate;
          const isToday = str === today;
          return (
            <button
              key={str}
              onClick={() => onSelect(str)}
              style={{
                minWidth: 52,
                padding: "8px 6px",
                borderRadius: 12,
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                gap: 2,
                background: active ? "var(--accent)" : "var(--surface)",
                color: active ? "#FFFFFF" : "var(--ink)",
                border: active ? "1px solid var(--accent)" : "1px solid var(--border)",
              }}
            >
              <span style={{ fontSize: 11, opacity: 0.85 }}>{WEEKDAYS[dow]}</span>
              <span style={{ fontSize: 15, fontWeight: 700 }}>{dayOfMonth}</span>
              {isToday && !active && (
                <span style={{ width: 4, height: 4, borderRadius: "50%", background: "var(--accent)" }} />
              )}
            </button>
          );
        })}
      </div>

      <button className="btn-secondary" onClick={() => onSelect(shiftDateStr(selectedDate, 1))} aria-label="Next day">
        →
      </button>
    </div>
  );
}
