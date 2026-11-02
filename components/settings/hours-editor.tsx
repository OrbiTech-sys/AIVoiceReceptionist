"use client";

import { Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { useToast } from "@/components/toast";
import { Button, ErrorNote, Field, Input, Select, Skeleton } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { keyLabel, WEEKDAYS } from "@/lib/format";
import { useApi, useEditable } from "@/lib/hooks";

interface Day {
  weekday: number;
  is_closed: boolean;
  open_time: string | null;
  close_time: string | null;
  break_start: string | null;
  break_end: string | null;
}
interface Holiday { id: string; date: string; label: string; is_closed: boolean; open_time: string | null; close_time: string | null }

export function HoursEditor({ submitLabel = "Save hours", onSaved }: { submitLabel?: string; onSaved?: () => void }) {
  const toast = useToast();
  const { data, error, reload } = useApi<{ days: Day[]; holidays: Holiday[]; timezone: string }>("/api/business/hours");
  const profile = useApi<{ after_hours_behavior: string }>("/api/business");
  const [days, setDays] = useEditable<Day[]>(data?.days);
  const [afterHoursRaw, setAfterHours] = useEditable<string>(profile.data?.after_hours_behavior);
  const afterHours = afterHoursRaw ?? "take_message";
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [hol, setHol] = useState({ date: "", label: "", is_closed: true, open_time: "10:00", close_time: "14:00" });

  if (error) return <ErrorNote error={error} onRetry={() => reload()} />;
  if (!days || !data) return <Skeleton className="h-96" />;

  const update = (i: number, patch: Partial<Day>) => setDays(days.map((d, j) => (j === i ? { ...d, ...patch } : d)));
  const copyMonday = () => setDays(days.map((d, i) => (i > 0 && i < 5 ? { ...days[0], weekday: d.weekday } : d)));

  const save = async () => {
    setBusy(true);
    setErr(null);
    try {
      const clean = days.map((d) => ({ ...d, break_start: d.break_start || null, break_end: d.break_end || null }));
      await api("/api/business/hours", { method: "PUT", body: { days: clean } });
      await api("/api/business", { method: "PATCH", body: { after_hours_behavior: afterHours } });
      toast.success("Working hours saved");
      onSaved?.();
    } catch (e) {
      setErr(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const addHoliday = async () => {
    if (!hol.date) return setErr("Pick a date for the exception.");
    try {
      await api("/api/business/holidays", { body: hol });
      setHol({ ...hol, date: "", label: "" });
      reload(true);
    } catch (e) {
      setErr(errorMessage(e));
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <div className="mb-2 flex items-center justify-between text-[13px] text-muted">
          <span>Times are in {data.timezone.replace(/_/g, " ")}.</span>
          <button className="font-medium text-accent hover:underline" onClick={copyMonday} type="button">Copy Monday to weekdays</button>
        </div>
        <div className="divide-y divide-border rounded-lg border border-border">
          {days.map((d, i) => (
            <div key={d.weekday} className="flex flex-wrap items-center gap-x-3 gap-y-2 px-3 py-2.5">
              <div className="w-24 text-sm font-medium">{WEEKDAYS[d.weekday]}</div>
              <label className="flex items-center gap-2 text-[13px] text-muted">
                <input
                  type="checkbox"
                  className="accent-[var(--accent)]"
                  checked={!d.is_closed}
                  onChange={(e) =>
                    update(i, e.target.checked
                      ? { is_closed: false, open_time: d.open_time ?? "09:00", close_time: d.close_time ?? "17:00" }
                      : { is_closed: true })
                  }
                />
                Open
              </label>
              {!d.is_closed ? (
                <>
                  <div className="flex items-center gap-1.5">
                    <Input aria-label={`${WEEKDAYS[d.weekday]} opens`} type="time" className="w-28" value={d.open_time ?? ""} onChange={(e) => update(i, { open_time: e.target.value })} />
                    <span className="text-muted">–</span>
                    <Input aria-label={`${WEEKDAYS[d.weekday]} closes`} type="time" className="w-28" value={d.close_time ?? ""} onChange={(e) => update(i, { close_time: e.target.value })} />
                  </div>
                  <div className="flex items-center gap-1.5 text-[13px] text-muted">
                    <span>Break</span>
                    <Input aria-label={`${WEEKDAYS[d.weekday]} break start`} type="time" className="w-28" value={d.break_start ?? ""} onChange={(e) => update(i, { break_start: e.target.value || null })} />
                    <span>–</span>
                    <Input aria-label={`${WEEKDAYS[d.weekday]} break end`} type="time" className="w-28" value={d.break_end ?? ""} onChange={(e) => update(i, { break_end: e.target.value || null })} />
                  </div>
                </>
              ) : (
                <span className="text-[13px] text-subtle">Closed</span>
              )}
            </div>
          ))}
        </div>
      </div>

      <Field label="After-hours behaviour" htmlFor="after" hint="What the receptionist does when a call comes in while you&apos;re closed.">
        <Select id="after" value={afterHours} onChange={(e) => setAfterHours(e.target.value)} className="sm:max-w-md">
          <option value="take_message">Book appointments and take messages</option>
          <option value="book_only">Book appointments only</option>
          <option value="closed_message">Tell callers you&apos;re closed, still offer booking</option>
        </Select>
      </Field>

      {err && <p role="alert" className="text-[13px] text-danger">{err}</p>}
      <Button variant="primary" onClick={save} loading={busy}>{submitLabel}</Button>

      <div className="border-t border-border pt-5">
        <h3 className="text-sm font-semibold">Holidays & exceptions</h3>
        <p className="mt-0.5 text-[13px] text-muted">Closed days or special hours override the weekly schedule.</p>
        <div className="mt-3 flex flex-wrap items-end gap-2">
          <Field label="Date" htmlFor="h-date"><Input id="h-date" type="date" value={hol.date} onChange={(e) => setHol({ ...hol, date: e.target.value })} /></Field>
          <Field label="Label" htmlFor="h-label"><Input id="h-label" placeholder="e.g. Thanksgiving" value={hol.label} onChange={(e) => setHol({ ...hol, label: e.target.value })} /></Field>
          <Field label="Hours" htmlFor="h-closed">
            <Select id="h-closed" value={hol.is_closed ? "closed" : "special"} onChange={(e) => setHol({ ...hol, is_closed: e.target.value === "closed" })}>
              <option value="closed">Closed all day</option>
              <option value="special">Special hours</option>
            </Select>
          </Field>
          {!hol.is_closed && (
            <div className="flex items-center gap-1.5 pb-0.5">
              <Input aria-label="Opens" type="time" className="w-28" value={hol.open_time} onChange={(e) => setHol({ ...hol, open_time: e.target.value })} />
              <Input aria-label="Closes" type="time" className="w-28" value={hol.close_time} onChange={(e) => setHol({ ...hol, close_time: e.target.value })} />
            </div>
          )}
          <Button icon={<Plus className="size-4" />} onClick={addHoliday} type="button">Add</Button>
        </div>
        {data.holidays.length > 0 && (
          <ul className="mt-3 divide-y divide-border rounded-lg border border-border">
            {data.holidays.map((h) => (
              <li key={h.id} className="flex items-center justify-between px-3 py-2 text-sm">
                <span>
                  <span className="font-medium">{keyLabel(h.date, { weekday: "short", month: "short", day: "numeric", year: "numeric" })}</span>
                  {h.label && <span className="text-muted"> · {h.label}</span>}
                  <span className="text-muted"> · {h.is_closed ? "Closed" : `${h.open_time}–${h.close_time}`}</span>
                </span>
                <button
                  aria-label="Remove exception"
                  className="rounded p-1 text-muted hover:text-danger"
                  onClick={async () => {
                    await api(`/api/business/holidays/${h.id}`, { method: "DELETE" });
                    reload(true);
                  }}
                >
                  <Trash2 className="size-4" />
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
