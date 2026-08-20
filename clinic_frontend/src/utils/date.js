// Pure UTC date arithmetic -- a date STRING with a time component
// (`new Date(`${d}T00:00:00`)`) parses as LOCAL time per the JS spec, while
// .toISOString() always serializes back in UTC; that silent local<->UTC
// round-trip caused a real day-shift bug fixed earlier in SecretaryView.
// Every date picker in the app shares these instead of re-deriving its own.
export function shiftDateStr(dateStr, delta) {
  const [year, month, day] = dateStr.split("-").map(Number);
  const utc = new Date(Date.UTC(year, month - 1, day));
  utc.setUTCDate(utc.getUTCDate() + delta);
  return utc.toISOString().slice(0, 10);
}

export function todayStr() {
  return new Date().toISOString().slice(0, 10);
}
