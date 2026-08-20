import { useState, useEffect } from "react";
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from "recharts";
import { apiFetch, downloadMonthlyReportPdf } from "../api";
import StatTile from "../components/StatTile";
import TrendChart from "../components/TrendChart";

export default function MonthlyAnalytics({ token }) {
  const now = new Date();
  const [year, setYear] = useState(now.getFullYear());
  const [month, setMonth] = useState(now.getMonth() + 1);
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [pdfBusy, setPdfBusy] = useState(false);
  const [pdfError, setPdfError] = useState("");

  function handleDownloadPdf() {
    setPdfBusy(true);
    setPdfError("");
    downloadMonthlyReportPdf(token, year, month)
      .catch((e) => setPdfError(e.message))
      .finally(() => setPdfBusy(false));
  }

  const [visitsTrend, setVisitsTrend] = useState([]);
  const [trendError, setTrendError] = useState("");

  useEffect(() => {
    apiFetch(`/api/doctor/analytics/monthly?year=${year}&month=${month}`, token)
      .then((d) => {
        setData(d);
        setError("");
      })
      .catch((e) => setError(e.message));
  }, [year, month, token]);

  // Rolling 14-day visit trend, independent of the month navigator above.
  useEffect(() => {
    apiFetch("/api/doctor/visits-trend?days=14", token)
      .then((d) => {
        setVisitsTrend(d);
        setTrendError("");
      })
      .catch((e) => setTrendError(e.message));
  }, [token]);

  function shiftMonth(delta) {
    let m = month + delta;
    let y = year;
    if (m < 1) {
      m = 12;
      y -= 1;
    } else if (m > 12) {
      m = 1;
      y += 1;
    }
    setMonth(m);
    setYear(y);
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      <div className="card" style={{ display: "flex", flexDirection: "column", gap: 16 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <button className="btn-secondary" onClick={() => shiftMonth(-1)}>←</button>
          <strong>{year}-{String(month).padStart(2, "0")}</strong>
          <button className="btn-secondary" onClick={() => shiftMonth(1)}>→</button>
          <button className="btn-secondary" style={{ marginLeft: "auto" }} disabled={pdfBusy} onClick={handleDownloadPdf}>
            {pdfBusy ? "Preparing…" : "📄 Download PDF"}
          </button>
        </div>

        {error && (
          <div style={{ background: "var(--danger-soft)", color: "var(--danger)", padding: "8px 12px", borderRadius: 8, fontSize: 13 }}>
            {error}
          </div>
        )}
        {pdfError && (
          <div style={{ background: "var(--danger-soft)", color: "var(--danger)", padding: "8px 12px", borderRadius: 8, fontSize: 13 }}>
            {pdfError}
          </div>
        )}

        {data && (
          <div style={{ display: "flex", gap: 14, flexWrap: "wrap" }}>
            <StatTile label="Revenue" value={`${data.total_revenue} EGP`} accent />
            <StatTile label="Payments" value={data.paid_count} />
            <StatTile label="Patients seen" value={data.distinct_patients_charged} />
            <StatTile label="Discounts given" value={`${data.total_discounts_given} EGP`} />
            <StatTile label="Insurance covered" value={`${data.total_insurance_covered} EGP`} />
          </div>
        )}
      </div>

      {data && (
        <div className="card" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <label style={{ fontSize: 13, fontWeight: 600 }}>Patient activity &amp; trends</label>
          <div style={{ display: "flex", gap: 14, flexWrap: "wrap" }}>
            <StatTile
              label={`vs. ${data.previous_month}`}
              value={
                data.revenue_change_pct === null
                  ? `${data.revenue_change >= 0 ? "+" : ""}${data.revenue_change} EGP`
                  : `${data.revenue_change_pct >= 0 ? "+" : ""}${data.revenue_change_pct}%`
              }
            />
            <StatTile label="New patients" value={data.new_patients} />
            <StatTile label="Returning patients" value={data.returning_patients} />
            <StatTile label="Inactive 6+ months" value={data.inactive_patients_count} />
            <StatTile label="No-shows this month" value={data.no_show_count} />
            <StatTile label="Est. lost to no-shows" value={`${data.no_show_estimated_lost_revenue} EGP`} />
          </div>
        </div>
      )}

      <div className="card">
        <label style={{ fontSize: 13, fontWeight: 600 }}>Visits — last 14 days</label>
        {trendError && (
          <div style={{ background: "var(--danger-soft)", color: "var(--danger)", padding: "8px 12px", borderRadius: 8, fontSize: 13, marginTop: 8 }}>
            {trendError}
          </div>
        )}
        {visitsTrend.length > 0 && <TrendChart data={visitsTrend} dataKey="visits" />}
      </div>

      {data && (
        <div className="card">
          <label style={{ fontSize: 13, fontWeight: 600 }}>Top procedures</label>
          {data.procedure_breakdown.length === 0 && (
            <div style={{ color: "var(--ink-soft)", marginTop: 6, fontSize: 13 }}>No procedures posted this month.</div>
          )}
          {data.procedure_breakdown.length > 0 && (
            <ResponsiveContainer width="100%" height={Math.max(120, data.procedure_breakdown.length * 44)}>
              <BarChart
                data={data.procedure_breakdown}
                layout="vertical"
                margin={{ top: 8, right: 24, bottom: 0, left: 0 }}
              >
                <CartesianGrid stroke="var(--border)" horizontal={false} />
                <XAxis type="number" tick={{ fontSize: 12, fill: "var(--ink-soft)" }} axisLine={{ stroke: "var(--border)" }} tickLine={false} />
                <YAxis
                  dataKey="procedure"
                  type="category"
                  width={140}
                  tick={{ fontSize: 13, fill: "var(--ink)" }}
                  axisLine={false}
                  tickLine={false}
                />
                <Tooltip
                  formatter={(v, name, props) => [`${v} EGP`, `× ${props.payload.count}`]}
                  contentStyle={{ borderRadius: 8, border: "1px solid var(--border)", fontSize: 13 }}
                />
                <Bar dataKey="revenue" fill="var(--accent)" radius={[0, 4, 4, 0]} barSize={22} />
              </BarChart>
            </ResponsiveContainer>
          )}
        </div>
      )}

      {data && (
        <div className="card">
          <label style={{ fontSize: 13, fontWeight: 600 }}>Insights</label>
          <div style={{ marginTop: 6, whiteSpace: "pre-wrap", direction: "rtl", textAlign: "right" }}>{data.insights}</div>
        </div>
      )}
    </div>
  );
}
