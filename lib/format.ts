// Display helpers. All appointment times are shown in the business time zone.

export function fmtDateTime(iso: string | null | undefined, tz: string, opts: Intl.DateTimeFormatOptions = {}) {
  if (!iso) return "—";
  return new Intl.DateTimeFormat(undefined, {
    timeZone: tz,
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    ...opts,
  }).format(new Date(iso));
}

export function fmtTime(iso: string, tz: string) {
  return new Intl.DateTimeFormat(undefined, { timeZone: tz, hour: "numeric", minute: "2-digit" }).format(new Date(iso));
}

export function fmtDate(iso: string, tz: string, opts: Intl.DateTimeFormatOptions = {}) {
  return new Intl.DateTimeFormat(undefined, { timeZone: tz, weekday: "short", month: "short", day: "numeric", ...opts }).format(
    new Date(iso),
  );
}

/** YYYY-MM-DD of an instant in a time zone. */
export function localDateKey(iso: string | Date, tz: string) {
  const d = typeof iso === "string" ? new Date(iso) : iso;
  const parts = new Intl.DateTimeFormat("en-CA", { timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(d);
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? "";
  return `${get("year")}-${get("month")}-${get("day")}`;
}

export function todayKey(tz: string) {
  return localDateKey(new Date(), tz);
}

export function addDays(key: string, n: number) {
  const [y, m, d] = key.split("-").map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d + n));
  return dt.toISOString().slice(0, 10);
}

export function weekdayOf(key: string) {
  const [y, m, d] = key.split("-").map(Number);
  return (new Date(Date.UTC(y, m - 1, d)).getUTCDay() + 6) % 7; // 0 = Monday
}

export function keyLabel(key: string, opts: Intl.DateTimeFormatOptions = { weekday: "short", month: "short", day: "numeric" }) {
  const [y, m, d] = key.split("-").map(Number);
  return new Intl.DateTimeFormat(undefined, { timeZone: "UTC", ...opts }).format(new Date(Date.UTC(y, m - 1, d)));
}

/** Minutes since local midnight of an instant in a zone. */
export function localMinutes(iso: string, tz: string) {
  const parts = new Intl.DateTimeFormat("en-GB", { timeZone: tz, hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).formatToParts(new Date(iso));
  const h = Number(parts.find((p) => p.type === "hour")?.value ?? 0);
  const m = Number(parts.find((p) => p.type === "minute")?.value ?? 0);
  return h * 60 + m;
}

/** Convert a local wall-clock date+time in tz to a UTC ISO string. */
export function zonedToUtcIso(dateKey: string, hhmm: string, tz: string) {
  const [y, mo, d] = dateKey.split("-").map(Number);
  const [h, mi] = hhmm.split(":").map(Number);
  const guess = Date.UTC(y, mo - 1, d, h, mi);
  // Find the offset of tz at the guessed instant, then correct (two passes handle DST edges).
  let ts = guess;
  for (let i = 0; i < 2; i++) {
    const p = new Intl.DateTimeFormat("en-GB", {
      timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
    }).formatToParts(new Date(ts));
    const g = (t: string) => Number(p.find((x) => x.type === t)?.value);
    const asUtc = Date.UTC(g("year"), g("month") - 1, g("day"), g("hour"), g("minute"));
    ts = ts + (guess - asUtc);
  }
  return new Date(ts).toISOString();
}

export function relTime(iso: string) {
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
  const abs = Math.abs(diff);
  if (abs < 60) return "just now";
  if (abs < 3600) return rtf.format(-Math.round(diff / 60), "minute");
  if (abs < 86400) return rtf.format(-Math.round(diff / 3600), "hour");
  return rtf.format(-Math.round(diff / 86400), "day");
}

export function fmtDuration(sec: number | null) {
  if (sec === null || sec === undefined) return "—";
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return m ? `${m}m ${s.toString().padStart(2, "0")}s` : `${s}s`;
}

export const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

export const STATUS_LABEL: Record<string, string> = {
  pending: "Pending",
  confirmed: "Confirmed",
  completed: "Completed",
  cancelled: "Cancelled",
  no_show: "No-show",
};

export const SOURCE_LABEL: Record<string, string> = { ai_call: "AI call", dashboard: "Dashboard", manual: "Manual entry" };

export const INTENT_LABEL: Record<string, string> = {
  booking: "Booking",
  reschedule: "Reschedule",
  cancellation: "Cancellation",
  callback: "Callback",
  urgent: "Urgent",
  transfer: "Transfer",
  appointment_question: "Appointment question",
  pricing_question: "Pricing",
  hours_question: "Hours",
  location_question: "Location",
  services_question: "Services",
  general_question: "General question",
  unknown: "Unknown",
};
