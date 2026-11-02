"use client";

import { CalendarDays, ChevronLeft, ChevronRight, Plus, Search, Sparkles } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";
import { NewAppointmentModal, RescheduleModal } from "@/components/appointment-forms";
import { useSession } from "@/components/session";
import { useToast } from "@/components/toast";
import { Badge, Button, Card, cx, Drawer, EmptyState, ErrorNote, Input, PageHeader, Select, Skeleton, statusTone, Textarea } from "@/components/ui";
import { api, errorMessage, qs } from "@/lib/api";
import { addDays, fmtDateTime, fmtTime, keyLabel, localDateKey, localMinutes, SOURCE_LABEL, STATUS_LABEL, todayKey, weekdayOf } from "@/lib/format";
import { useApi, useDebounced, useQueryParam } from "@/lib/hooks";
import type { Appointment, Paged, Service, Staff } from "@/lib/types";

type View = "day" | "week" | "month" | "list";
const HOUR_START = 7;
const HOUR_END = 20;
const PX_PER_MIN = 0.9;

function rangeFor(view: View, anchor: string): [string, string] {
  if (view === "day") return [anchor, anchor];
  if (view === "week" || view === "list") {
    const start = addDays(anchor, -weekdayOf(anchor));
    return [start, addDays(start, view === "list" ? 30 : 6)];
  }
  const first = anchor.slice(0, 8) + "01";
  const gridStart = addDays(first, -weekdayOf(first));
  return [gridStart, addDays(gridStart, 41)];
}

function ApptChip({ a, tz, onClick }: { a: Appointment; tz: string; onClick: () => void }) {
  const cancelled = a.status === "cancelled" || a.status === "no_show";
  return (
    <button onClick={onClick}
      className={cx("w-full truncate rounded-md border-l-2 px-1.5 py-0.5 text-left text-[11px]",
        cancelled ? "border-danger/50 bg-surface-2 text-subtle line-through" : a.status === "pending" ? "border-warning bg-warning-soft" : "border-accent bg-accent-soft")}>
      <span className="font-medium tabular-nums">{fmtTime(a.start_at, tz)}</span> {a.contact.name}
    </button>
  );
}

function TimeGrid({ days, appts, tz, onOpen }: { days: string[]; appts: Appointment[]; tz: string; onOpen: (a: Appointment) => void }) {
  const hours = Array.from({ length: HOUR_END - HOUR_START }, (_, i) => HOUR_START + i);
  const height = (HOUR_END - HOUR_START) * 60 * PX_PER_MIN;
  const today = todayKey(tz);
  return (
    <div className="overflow-x-auto">
      <div className="min-w-[640px]">
        <div className="grid border-b border-border" style={{ gridTemplateColumns: `3.5rem repeat(${days.length}, 1fr)` }}>
          <div />
          {days.map((d) => (
            <div key={d} className={cx("px-2 py-2 text-center text-[13px]", d === today ? "font-semibold text-accent" : "text-muted")}>{keyLabel(d)}</div>
          ))}
        </div>
        <div className="relative grid" style={{ gridTemplateColumns: `3.5rem repeat(${days.length}, 1fr)`, height }}>
          <div className="relative">
            {hours.map((h) => (
              <div key={h} className="absolute right-2 -translate-y-2 text-[11px] text-subtle" style={{ top: (h - HOUR_START) * 60 * PX_PER_MIN }}>
                {new Intl.DateTimeFormat(undefined, { hour: "numeric" }).format(new Date(2000, 0, 1, h))}
              </div>
            ))}
          </div>
          {days.map((d) => (
            <div key={d} className="relative border-l border-border">
              {hours.map((h) => <div key={h} className="absolute inset-x-0 border-t border-border/60" style={{ top: (h - HOUR_START) * 60 * PX_PER_MIN }} />)}
              {appts.filter((a) => localDateKey(a.start_at, tz) === d).map((a) => {
                const top = Math.max(0, (localMinutes(a.start_at, tz) - HOUR_START * 60) * PX_PER_MIN);
                const h = Math.max(22, (new Date(a.end_at).getTime() - new Date(a.start_at).getTime()) / 60000 * PX_PER_MIN);
                const cancelled = a.status === "cancelled" || a.status === "no_show";
                return (
                  <button key={a.id} onClick={() => onOpen(a)} style={{ top, height: h }}
                    className={cx("absolute inset-x-1 overflow-hidden rounded-md border-l-2 px-1.5 py-1 text-left text-[11px] leading-tight",
                      cancelled ? "border-danger/50 bg-surface-2 text-subtle line-through" : a.status === "pending" ? "border-warning bg-warning-soft" : "border-accent bg-accent-soft hover:brightness-95")}>
                    <div className="truncate font-medium">{a.contact.name}</div>
                    <div className="truncate text-muted">{fmtTime(a.start_at, tz)} · {a.service.name}</div>
                  </button>
                );
              })}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function MonthGrid({ start, anchor, appts, tz, onOpen, onDay }: { start: string; anchor: string; appts: Appointment[]; tz: string; onOpen: (a: Appointment) => void; onDay: (d: string) => void }) {
  const days = Array.from({ length: 42 }, (_, i) => addDays(start, i));
  const month = anchor.slice(0, 7);
  const today = todayKey(tz);
  const byDay = useMemo(() => {
    const m: Record<string, Appointment[]> = {};
    for (const a of appts) (m[localDateKey(a.start_at, tz)] ??= []).push(a);
    return m;
  }, [appts, tz]);
  return (
    <div className="overflow-x-auto">
      <div className="grid min-w-[640px] grid-cols-7 border-l border-t border-border">
        {["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((d) => <div key={d} className="border-b border-r border-border px-2 py-1.5 text-xs text-muted">{d}</div>)}
        {days.map((d) => {
          const list = byDay[d] ?? [];
          return (
            <div key={d} className={cx("min-h-24 border-b border-r border-border p-1", d.slice(0, 7) !== month && "bg-surface-2/50")}>
              <button onClick={() => onDay(d)} className={cx("mb-1 rounded px-1 text-xs", d === today ? "bg-accent font-semibold text-accent-fg" : "text-muted hover:text-text")}>{Number(d.slice(8))}</button>
              <div className="space-y-0.5">
                {list.slice(0, 3).map((a) => <ApptChip key={a.id} a={a} tz={tz} onClick={() => onOpen(a)} />)}
                {list.length > 3 && <button onClick={() => onDay(d)} className="px-1 text-[11px] text-muted hover:text-text">+{list.length - 3} more</button>}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function Detail({ appt, onClose, onChanged, onReschedule }: { appt: Appointment; onClose: () => void; onChanged: (a: Appointment) => void; onReschedule: () => void }) {
  const { tz } = useSession();
  const toast = useToast();
  const [busy, setBusy] = useState<string | null>(null);
  const [notes, setNotes] = useState(appt.notes);
  const active = appt.status === "pending" || appt.status === "confirmed";
  const started = new Date(appt.start_at) <= new Date();
  const setStatus = async (status: string) => {
    let reason = "";
    if (status === "cancelled") {
      const r = prompt("Reason for cancelling (optional)");
      if (r === null) return;
      reason = r;
    }
    setBusy(status);
    try {
      const a = await api<Appointment>(`/api/appointments/${appt.id}/status`, { body: { status, reason } });
      toast.success(`Marked as ${STATUS_LABEL[status].toLowerCase()}`);
      onChanged(a);
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setBusy(null);
    }
  };
  return (
    <Drawer open onClose={onClose} title={<span className="flex items-center gap-2">{appt.contact.name} <Badge tone={statusTone(appt.status)}>{STATUS_LABEL[appt.status]}</Badge></span>}
      footer={
        <>
          {appt.status === "pending" && <Button variant="primary" loading={busy === "confirmed"} onClick={() => setStatus("confirmed")}>Confirm</Button>}
          {active && <Button onClick={onReschedule}>Reschedule</Button>}
          {active && started && <Button loading={busy === "completed"} onClick={() => setStatus("completed")}>Completed</Button>}
          {active && started && <Button loading={busy === "no_show"} onClick={() => setStatus("no_show")}>No-show</Button>}
          {active && <Button variant="danger" loading={busy === "cancelled"} onClick={() => setStatus("cancelled")}>Cancel appointment</Button>}
        </>
      }>
      <dl className="grid grid-cols-3 gap-y-3 text-sm">
        <dt className="text-muted">When</dt><dd className="col-span-2">{fmtDateTime(appt.start_at, tz)} – {fmtTime(appt.end_at, tz)}</dd>
        <dt className="text-muted">Service</dt><dd className="col-span-2">{appt.service.name} ({appt.service.duration_min} min)</dd>
        <dt className="text-muted">Staff</dt><dd className="col-span-2">{appt.staff?.name ?? "—"}</dd>
        <dt className="text-muted">Phone</dt><dd className="col-span-2">{appt.contact.phone_display || "—"}</dd>
        {appt.contact.email && <><dt className="text-muted">E-mail</dt><dd className="col-span-2">{appt.contact.email}</dd></>}
        <dt className="text-muted">Booked via</dt><dd className="col-span-2 flex items-center gap-1.5">{appt.source === "ai_call" && <Sparkles className="size-3.5 text-accent" />}{SOURCE_LABEL[appt.source]} · {fmtDateTime(appt.created_at, tz)}</dd>
        {appt.cancel_reason && <><dt className="text-muted">Cancel reason</dt><dd className="col-span-2">{appt.cancel_reason}</dd></>}
      </dl>
      <div className="mt-4 flex gap-3 text-[13px]">
        <Link className="font-medium text-accent hover:underline" href={`/contacts?id=${appt.contact.id}`}>Contact history</Link>
        {appt.call_id && <Link className="font-medium text-accent hover:underline" href={`/calls?id=${appt.call_id}`}>View call</Link>}
      </div>
      <div className="mt-5">
        <label htmlFor="ap-notes" className="text-[13px] font-medium">Notes</label>
        <Textarea id="ap-notes" className="mt-1.5" rows={3} value={notes} onChange={(e) => setNotes(e.target.value)} />
        <Button size="sm" className="mt-2" disabled={notes === appt.notes} loading={busy === "notes"} onClick={async () => {
          setBusy("notes");
          try {
            onChanged(await api<Appointment>(`/api/appointments/${appt.id}`, { method: "PATCH", body: { notes } }));
            toast.success("Notes saved");
          } catch (e) { toast.error(errorMessage(e)); } finally { setBusy(null); }
        }}>Save notes</Button>
      </div>
    </Drawer>
  );
}

export default function AppointmentsPage() {
  const { tz } = useSession();
  const [view, setView] = useState<View>("week");
  const [anchor, setAnchor] = useState(() => todayKey(tz));
  const [status, setStatus] = useState("pending,confirmed,completed,no_show");
  const [staffId, setStaffId] = useState("");
  const [serviceId, setServiceId] = useState("");
  const [search, setSearch] = useState("");
  const q = useDebounced(search);
  const [openId, setOpenId] = useQueryParam("id");
  const [creating, setCreating] = useState(false);
  const [rescheduling, setRescheduling] = useState<Appointment | null>(null);
  const [start, end] = rangeFor(view, anchor);

  const path = `/api/appointments${qs({ start: view === "list" && !q ? todayKey(tz) : view === "list" ? undefined : start, end: view === "list" ? undefined : end, status, staff_id: staffId, service_id: serviceId, q, limit: 500 })}`;
  const { data, error, loading, reload } = useApi<Paged<Appointment>>(path, { poll: 30000 });
  const staff = useApi<Staff[]>("/api/staff");
  const services = useApi<Service[]>("/api/services");
  const detail = useApi<Appointment>(openId ? `/api/appointments/${openId}` : null);
  const appts = data?.items ?? [];

  const step = (dir: number) => {
    if (view === "day") setAnchor(addDays(anchor, dir));
    else if (view === "month") {
      const [y, m] = anchor.split("-").map(Number);
      const d = new Date(Date.UTC(y, m - 1 + dir, 1));
      setAnchor(d.toISOString().slice(0, 10));
    } else setAnchor(addDays(anchor, 7 * dir));
  };
  const title = view === "month" ? keyLabel(anchor, { month: "long", year: "numeric" }) : view === "day" ? keyLabel(anchor, { weekday: "long", month: "long", day: "numeric" }) : `${keyLabel(start, { month: "short", day: "numeric" })} – ${keyLabel(addDays(start, 6), { month: "short", day: "numeric", year: "numeric" })}`;
  const changed = (a: Appointment) => {
    detail.setData(a);
    reload(true);
  };

  return (
    <>
      <PageHeader title="Appointments" description={`All times in ${tz.replace(/_/g, " ")}.`}
        actions={<Button variant="primary" icon={<Plus className="size-4" />} onClick={() => setCreating(true)}>New appointment</Button>} />
      <Card>
        <div className="mb-4 flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex flex-wrap items-center gap-2">
            <div className="inline-flex rounded-lg border border-border p-0.5" role="group" aria-label="Calendar view">
              {(["day", "week", "month", "list"] as View[]).map((v) => (
                <button key={v} onClick={() => setView(v)} aria-pressed={view === v}
                  className={cx("rounded-md px-2.5 py-1 text-[13px] capitalize", view === v ? "bg-surface-2 font-medium" : "text-muted hover:text-text")}>{v}</button>
              ))}
            </div>
            {view !== "list" && (
              <>
                <Button size="sm" onClick={() => step(-1)} aria-label="Previous"><ChevronLeft className="size-4" /></Button>
                <Button size="sm" onClick={() => setAnchor(todayKey(tz))}>Today</Button>
                <Button size="sm" onClick={() => step(1)} aria-label="Next"><ChevronRight className="size-4" /></Button>
                <span className="text-sm font-medium">{title}</span>
              </>
            )}
          </div>
          <div className="flex flex-wrap gap-2">
            <div className="relative">
              <Search className="pointer-events-none absolute left-2.5 top-2.5 size-4 text-subtle" />
              <Input aria-label="Search by name or phone" placeholder="Name or phone" value={search} onChange={(e) => setSearch(e.target.value)} className="w-44 pl-8" />
            </div>
            <Select aria-label="Status filter" value={status} onChange={(e) => setStatus(e.target.value)} className="w-40">
              <option value="pending,confirmed,completed,no_show">Hide cancelled</option>
              <option value="pending,confirmed">Upcoming / active</option>
              <option value="pending">Pending</option>
              <option value="confirmed">Confirmed</option>
              <option value="completed">Completed</option>
              <option value="cancelled">Cancelled</option>
              <option value="no_show">No-show</option>
              <option value="">All statuses</option>
            </Select>
            <Select aria-label="Staff filter" value={staffId} onChange={(e) => setStaffId(e.target.value)} className="w-36">
              <option value="">All staff</option>
              {(staff.data ?? []).map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </Select>
            <Select aria-label="Service filter" value={serviceId} onChange={(e) => setServiceId(e.target.value)} className="w-40">
              <option value="">All services</option>
              {(services.data ?? []).map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </Select>
          </div>
        </div>
        <ErrorNote error={error} onRetry={() => reload()} />
        {loading && !data ? <Skeleton className="h-96" /> : view === "list" ? (
          appts.length ? (
            <div className="-mx-4 overflow-x-auto sm:-mx-5">
              <table className="w-full min-w-[640px] text-sm">
                <thead className="border-y border-border bg-surface-2/50 text-left text-xs text-muted">
                  <tr><th className="px-4 py-2 font-medium sm:px-5">When</th><th className="px-3 py-2 font-medium">Customer</th><th className="px-3 py-2 font-medium">Service</th><th className="px-3 py-2 font-medium">Staff</th><th className="px-3 py-2 font-medium">Source</th><th className="px-3 py-2 font-medium">Status</th></tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {appts.map((a) => (
                    <tr key={a.id} className="cursor-pointer hover:bg-surface-2/60" onClick={() => setOpenId(a.id)}>
                      <td className="whitespace-nowrap px-4 py-2.5 tabular-nums sm:px-5">{fmtDateTime(a.start_at, tz)}</td>
                      <td className="px-3 py-2.5"><div className="font-medium">{a.contact.name}</div><div className="text-xs text-muted">{a.contact.phone_display}</div></td>
                      <td className="px-3 py-2.5">{a.service.name}</td>
                      <td className="px-3 py-2.5 text-muted">{a.staff?.name ?? "—"}</td>
                      <td className="px-3 py-2.5 text-muted">{SOURCE_LABEL[a.source]}</td>
                      <td className="px-3 py-2.5"><Badge tone={statusTone(a.status)}>{STATUS_LABEL[a.status]}</Badge></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : <EmptyState icon={<CalendarDays className="size-5" />} title={q ? "No matching appointments" : "No upcoming appointments"} description="Create one, or let the receptionist book them for you." action={<Button onClick={() => setCreating(true)}>New appointment</Button>} />
        ) : view === "month" ? (
          <MonthGrid start={start} anchor={anchor} appts={appts} tz={tz} onOpen={(a) => setOpenId(a.id)} onDay={(d) => { setAnchor(d); setView("day"); }} />
        ) : (
          <TimeGrid days={view === "day" ? [anchor] : Array.from({ length: 7 }, (_, i) => addDays(start, i))} appts={appts} tz={tz} onOpen={(a) => setOpenId(a.id)} />
        )}
      </Card>

      {openId && detail.data && detail.data.id === openId && (
        <Detail key={detail.data.id + detail.data.status + detail.data.start_at} appt={detail.data} onClose={() => setOpenId(null)} onChanged={changed} onReschedule={() => setRescheduling(detail.data!)} />
      )}
      {openId && detail.error && <div className="mt-3"><ErrorNote error={detail.error} /></div>}
      <NewAppointmentModal open={creating} onClose={() => setCreating(false)} onCreated={(a) => { setCreating(false); reload(true); setOpenId(a.id); }} />
      <RescheduleModal appt={rescheduling} onClose={() => setRescheduling(null)} onDone={(a) => { setRescheduling(null); changed(a); }} />
    </>
  );
}
