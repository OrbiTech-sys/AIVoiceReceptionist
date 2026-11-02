"use client";

import { useState } from "react";
import { useSession } from "@/components/session";
import { useToast } from "@/components/toast";
import { Button, Field, Input, Modal, Select, Spinner, Switch, Textarea, cx } from "@/components/ui";
import { api, errorMessage, qs } from "@/lib/api";
import { addDays, fmtTime, keyLabel, todayKey, zonedToUtcIso } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import type { Appointment, Service, Slot, Staff } from "@/lib/types";

export function SlotPicker({ serviceId, staffId, excludeId, value, onChange }: {
  serviceId: string; staffId: string; excludeId?: string; value: Slot | null; onChange: (s: Slot | null) => void;
}) {
  const { tz } = useSession();
  const [day, setDay] = useState(todayKey(tz));
  const path = serviceId ? `/api/appointments/availability${qs({ service_id: serviceId, start: day, days: 1, staff_id: staffId || undefined, exclude_appointment_id: excludeId })}` : null;
  const { data, loading, error } = useApi<{ slots: Slot[] }>(path);
  const changeDay = (d: string) => {
    setDay(d);
    onChange(null);
  };
  return (
    <div>
      <div className="mb-2 flex items-center gap-2">
        <Button size="sm" type="button" onClick={() => changeDay(addDays(day, -1))} disabled={day <= todayKey(tz)} aria-label="Previous day">‹</Button>
        <Input type="date" value={day} min={todayKey(tz)} onChange={(e) => e.target.value && changeDay(e.target.value)} className="w-44" aria-label="Date" />
        <Button size="sm" type="button" onClick={() => changeDay(addDays(day, 1))} aria-label="Next day">›</Button>
        <span className="text-[13px] text-muted">{keyLabel(day, { weekday: "long" })}</span>
      </div>
      {!serviceId ? <p className="text-[13px] text-muted">Choose a service first.</p> : loading ? <Spinner /> : error ? (
        <p className="text-[13px] text-danger">{error.message}</p>
      ) : data && data.slots.length ? (
        <div className="grid max-h-48 grid-cols-3 gap-1.5 overflow-y-auto sm:grid-cols-4">
          {data.slots.map((s) => {
            const on = value?.start_at === s.start_at && value?.staff_id === s.staff_id;
            return (
              <button type="button" key={s.start_at + s.staff_id} onClick={() => onChange(s)}
                className={cx("rounded-lg border px-2 py-1.5 text-[13px] tabular-nums", on ? "border-accent bg-accent text-accent-fg" : "border-border hover:bg-surface-2")}>
                {fmtTime(s.start_at, tz)}
                {s.staff_name && <span className={cx("block truncate text-[11px]", on ? "text-accent-fg/80" : "text-muted")}>{s.staff_name}</span>}
              </button>
            );
          })}
        </div>
      ) : (
        <p className="text-[13px] text-muted">No available times on this day. Try another date.</p>
      )}
    </div>
  );
}

export function NewAppointmentModal({ open, onClose, onCreated, defaults }: {
  open: boolean; onClose: () => void; onCreated: (a: Appointment) => void; defaults?: { name?: string; phone?: string };
}) {
  return (
    <Modal wide open={open} onClose={onClose} title="New appointment">
      <NewAppointmentForm onCancel={onClose} onCreated={onCreated} defaults={defaults} />
    </Modal>
  );
}

function NewAppointmentForm({ onCancel, onCreated, defaults }: {
  onCancel: () => void; onCreated: (a: Appointment) => void; defaults?: { name?: string; phone?: string };
}) {
  const { tz } = useSession();
  const toast = useToast();
  const services = useApi<Service[]>("/api/services");
  const staff = useApi<Staff[]>("/api/staff");
  const [chosenService, setServiceId] = useState("");
  const [staffId, setStaffId] = useState("");
  const [slot, setSlot] = useState<Slot | null>(null);
  const [override, setOverride] = useState(false);
  const [manual, setManual] = useState({ date: todayKey(tz), time: "09:00" });
  const [c, setC] = useState({ name: defaults?.name ?? "", phone: defaults?.phone ?? "", email: "", notes: "" });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const serviceId = chosenService || services.data?.find((s) => s.is_active)?.id || "";

  const eligibleStaff = (staff.data ?? []).filter((s) => s.is_active && s.service_ids.includes(serviceId));
  const submit = async () => {
    setErr(null);
    if (!c.name.trim()) return setErr("Enter the customer's name.");
    const start = override ? zonedToUtcIso(manual.date, manual.time, tz) : slot?.start_at;
    if (!start) return setErr("Pick an available time.");
    setBusy(true);
    try {
      const a = await api<Appointment>("/api/appointments", {
        body: {
          service_id: serviceId, start_at: start, staff_id: (override ? staffId : slot?.staff_id) || null,
          customer_name: c.name, customer_phone: c.phone || null, customer_email: c.email, notes: c.notes, override_hours: override,
        },
      });
      toast.success("Appointment booked");
      onCreated(a);
    } catch (e) {
      setErr(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-4">
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Service" htmlFor="na-svc">
          <Select id="na-svc" value={serviceId} onChange={(e) => { setServiceId(e.target.value); setStaffId(""); setSlot(null); }}>
            {(services.data ?? []).filter((s) => s.is_active).map((s) => <option key={s.id} value={s.id}>{s.name} · {s.duration_min} min</option>)}
          </Select>
        </Field>
        <Field label="Staff" htmlFor="na-staff">
          <Select id="na-staff" value={staffId} onChange={(e) => { setStaffId(e.target.value); setSlot(null); }} disabled={!eligibleStaff.length}>
            <option value="">{eligibleStaff.length ? "Any available" : "Shared calendar"}</option>
            {eligibleStaff.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </Select>
        </Field>
      </div>
      <Switch checked={override} onChange={setOverride} label="Book outside configured availability" description="For manual entries. Overlapping bookings are still blocked." />
      {override ? (
        <div className="flex gap-2">
          <Field label="Date" htmlFor="na-date"><Input id="na-date" type="date" value={manual.date} onChange={(e) => setManual({ ...manual, date: e.target.value })} /></Field>
          <Field label="Time" htmlFor="na-time"><Input id="na-time" type="time" value={manual.time} onChange={(e) => setManual({ ...manual, time: e.target.value })} /></Field>
        </div>
      ) : (
        <Field label="Available times" htmlFor="">
          <SlotPicker serviceId={serviceId} staffId={staffId} value={slot} onChange={setSlot} />
        </Field>
      )}
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Customer name" htmlFor="na-name"><Input id="na-name" value={c.name} onChange={(e) => setC({ ...c, name: e.target.value })} /></Field>
        <Field label="Phone" htmlFor="na-phone" hint="Existing contacts are matched by phone number."><Input id="na-phone" type="tel" value={c.phone} onChange={(e) => setC({ ...c, phone: e.target.value })} /></Field>
        <Field label="E-mail (optional)" htmlFor="na-email"><Input id="na-email" type="email" value={c.email} onChange={(e) => setC({ ...c, email: e.target.value })} /></Field>
        <Field label="Notes (optional)" htmlFor="na-notes"><Textarea id="na-notes" rows={1} value={c.notes} onChange={(e) => setC({ ...c, notes: e.target.value })} /></Field>
      </div>
      {err && <p role="alert" className="text-[13px] text-danger">{err}</p>}
      <div className="flex justify-end gap-2 border-t border-border pt-3">
        <Button onClick={onCancel}>Cancel</Button>
        <Button variant="primary" loading={busy} onClick={submit}>Book appointment</Button>
      </div>
    </div>
  );
}

export function RescheduleModal({ appt, onClose, onDone }: { appt: Appointment | null; onClose: () => void; onDone: (a: Appointment) => void }) {
  const toast = useToast();
  const [slot, setSlot] = useState<Slot | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const submit = async () => {
    if (!appt || !slot) return setErr("Pick a new time.");
    setBusy(true);
    setErr(null);
    try {
      const a = await api<Appointment>(`/api/appointments/${appt.id}/reschedule`, { body: { start_at: slot.start_at, staff_id: slot.staff_id } });
      toast.success("Appointment rescheduled");
      onDone(a);
    } catch (e) {
      setErr(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal open={!!appt} onClose={onClose} title="Reschedule appointment"
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" loading={busy} onClick={submit} disabled={!slot}>Move appointment</Button></>}>
      {appt && (
        <div className="space-y-3">
          <p className="text-[13px] text-muted">{appt.contact.name} · {appt.service.name}</p>
          <SlotPicker serviceId={appt.service.id} staffId="" excludeId={appt.id} value={slot} onChange={setSlot} />
          {err && <p role="alert" className="text-[13px] text-danger">{err}</p>}
        </div>
      )}
    </Modal>
  );
}
