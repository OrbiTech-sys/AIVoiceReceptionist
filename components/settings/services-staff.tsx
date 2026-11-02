"use client";

import { Pencil, Plus, Stethoscope, Trash2, UserRound } from "lucide-react";
import { useState } from "react";
import { useToast } from "@/components/toast";
import { Badge, Button, EmptyState, ErrorNote, Field, Input, Modal, Skeleton, Textarea } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { WEEKDAYS } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import type { Service, Staff } from "@/lib/types";

export function ServicesEditor() {
  const toast = useToast();
  const { data, error, loading, reload } = useApi<Service[]>("/api/services");
  const [edit, setEdit] = useState<Partial<Service> | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const save = async () => {
    if (!edit?.name?.trim()) return setErr("Enter a service name.");
    const duration = Number(edit.duration_min);
    if (!duration || duration < 5) return setErr("Duration must be at least 5 minutes.");
    setBusy(true);
    setErr(null);
    const body = {
      name: edit.name, description: edit.description ?? "", duration_min: duration,
      buffer_min: edit.buffer_min === null || edit.buffer_min === undefined || String(edit.buffer_min) === "" ? null : Number(edit.buffer_min),
      price_text: edit.price_text ?? "", is_active: edit.is_active ?? true,
    };
    try {
      if (edit.id) await api(`/api/services/${edit.id}`, { method: "PUT", body });
      else await api("/api/services", { body });
      setEdit(null);
      reload(true);
    } catch (e) {
      setErr(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <div className="mb-3 flex items-center justify-between">
        <p className="text-[13px] text-muted">Services callers can book. Prices are only quoted if you enter them.</p>
        <Button size="sm" variant="primary" icon={<Plus className="size-4" />} onClick={() => setEdit({ duration_min: 30, is_active: true })}>Add service</Button>
      </div>
      <ErrorNote error={error} onRetry={() => reload()} />
      {loading && !data ? <Skeleton className="h-32" /> : data && data.length ? (
        <ul className="divide-y divide-border rounded-lg border border-border">
          {data.map((s) => (
            <li key={s.id} className="flex items-center gap-3 px-3 py-2.5">
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium">{s.name}</span>
                  {!s.is_active && <Badge tone="warning">Inactive</Badge>}
                </div>
                <div className="text-[13px] text-muted">
                  {s.duration_min} min{s.buffer_min ? ` + ${s.buffer_min} min buffer` : ""}{s.price_text ? ` · ${s.price_text}` : " · no price set"}
                </div>
              </div>
              <button aria-label={`Edit ${s.name}`} className="rounded p-1 text-muted hover:text-text" onClick={() => setEdit(s)}><Pencil className="size-4" /></button>
              <button
                aria-label={`Delete ${s.name}`}
                className="rounded p-1 text-muted hover:text-danger"
                onClick={async () => {
                  if (!confirm(`Remove ${s.name}?`)) return;
                  const r = await api<{ archived: boolean }>(`/api/services/${s.id}`, { method: "DELETE" });
                  if (r.archived) toast.success("Service has bookings, so it was set to inactive instead.");
                  reload(true);
                }}
              >
                <Trash2 className="size-4" />
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <div className="rounded-lg border border-dashed border-border">
          <EmptyState icon={<Stethoscope className="size-5" />} title="No services yet" description="Add at least one service so callers can book." />
        </div>
      )}
      <Modal open={!!edit} onClose={() => setEdit(null)} title={edit?.id ? "Edit service" : "New service"}
        footer={<><Button onClick={() => setEdit(null)}>Cancel</Button><Button variant="primary" loading={busy} onClick={save}>Save</Button></>}>
        {edit && (
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Name" htmlFor="s-name" className="sm:col-span-2"><Input id="s-name" value={edit.name ?? ""} onChange={(e) => setEdit({ ...edit, name: e.target.value })} /></Field>
            <Field label="Duration (minutes)" htmlFor="s-dur"><Input id="s-dur" type="number" min={5} max={600} value={edit.duration_min ?? ""} onChange={(e) => setEdit({ ...edit, duration_min: Number(e.target.value) })} /></Field>
            <Field label="Buffer after (minutes)" htmlFor="s-buf" hint="Leave empty to use the default."><Input id="s-buf" type="number" min={0} max={240} value={edit.buffer_min ?? ""} onChange={(e) => setEdit({ ...edit, buffer_min: e.target.value === "" ? null : Number(e.target.value) })} /></Field>
            <Field label="Price (optional)" htmlFor="s-price" hint='Spoken as written, e.g. "$120" or "from $80".' className="sm:col-span-2"><Input id="s-price" value={edit.price_text ?? ""} onChange={(e) => setEdit({ ...edit, price_text: e.target.value })} /></Field>
            <Field label="Description (optional)" htmlFor="s-desc" className="sm:col-span-2"><Textarea id="s-desc" rows={2} value={edit.description ?? ""} onChange={(e) => setEdit({ ...edit, description: e.target.value })} /></Field>
            <label className="flex items-center gap-2 text-sm"><input type="checkbox" className="accent-[var(--accent)]" checked={edit.is_active ?? true} onChange={(e) => setEdit({ ...edit, is_active: e.target.checked })} />Bookable</label>
            {err && <p className="text-[13px] text-danger sm:col-span-2">{err}</p>}
          </div>
        )}
      </Modal>
    </div>
  );
}

export function StaffEditor() {
  const { data, error, loading, reload } = useApi<Staff[]>("/api/staff");
  const services = useApi<Service[]>("/api/services");
  const [edit, setEdit] = useState<Partial<Staff> | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const save = async () => {
    if (!edit?.name?.trim()) return setErr("Enter a name.");
    setBusy(true);
    setErr(null);
    const body = { name: edit.name, title: edit.title ?? "", email: edit.email ?? "", is_active: edit.is_active ?? true,
      service_ids: edit.service_ids ?? [], availability: edit.availability ?? [] };
    try {
      if (edit.id) await api(`/api/staff/${edit.id}`, { method: "PUT", body });
      else await api("/api/staff", { body });
      setEdit(null);
      reload(true);
    } catch (e) {
      setErr(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const avail = edit?.availability ?? [];
  const dayAvail = (wd: number) => avail.find((a) => a.weekday === wd);
  const setDay = (wd: number, v: { start: string; end: string } | null) =>
    setEdit({ ...edit, availability: [...avail.filter((a) => a.weekday !== wd), ...(v ? [{ weekday: wd, ...v }] : [])].sort((a, b) => a.weekday - b.weekday) });

  const active = (data ?? []).filter((s) => s.is_active);
  return (
    <div>
      <div className="mb-3 flex items-center justify-between">
        <p className="text-[13px] text-muted">Optional. Link staff to services to give each person their own calendar.</p>
        <Button size="sm" variant="primary" icon={<Plus className="size-4" />} onClick={() => setEdit({ service_ids: [], availability: [], is_active: true })}>Add staff</Button>
      </div>
      <ErrorNote error={error} onRetry={() => reload()} />
      {loading && !data ? <Skeleton className="h-24" /> : active.length ? (
        <ul className="divide-y divide-border rounded-lg border border-border">
          {active.map((s) => (
            <li key={s.id} className="flex items-center gap-3 px-3 py-2.5">
              <div className="min-w-0 flex-1">
                <div className="text-sm font-medium">{s.name}{s.title && <span className="font-normal text-muted"> · {s.title}</span>}</div>
                <div className="text-[13px] text-muted">
                  {s.service_ids.length ? s.service_ids.map((id) => services.data?.find((x) => x.id === id)?.name).filter(Boolean).join(", ") : "No services linked"}
                  {" · "}{s.availability.length ? `${s.availability.length} working days` : "follows business hours"}
                </div>
              </div>
              <button aria-label={`Edit ${s.name}`} className="rounded p-1 text-muted hover:text-text" onClick={() => setEdit(s)}><Pencil className="size-4" /></button>
              <button aria-label={`Remove ${s.name}`} className="rounded p-1 text-muted hover:text-danger" onClick={async () => {
                if (!confirm(`Remove ${s.name}? Existing appointments are kept.`)) return;
                await api(`/api/staff/${s.id}`, { method: "DELETE" });
                reload(true);
              }}><Trash2 className="size-4" /></button>
            </li>
          ))}
        </ul>
      ) : (
        <div className="rounded-lg border border-dashed border-border">
          <EmptyState icon={<UserRound className="size-5" />} title="No staff calendars" description="Without staff, bookings use one shared calendar for the business." />
        </div>
      )}
      <Modal wide open={!!edit} onClose={() => setEdit(null)} title={edit?.id ? "Edit staff member" : "New staff member"}
        footer={<><Button onClick={() => setEdit(null)}>Cancel</Button><Button variant="primary" loading={busy} onClick={save}>Save</Button></>}>
        {edit && (
          <div className="space-y-4">
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Name" htmlFor="st-name"><Input id="st-name" value={edit.name ?? ""} onChange={(e) => setEdit({ ...edit, name: e.target.value })} /></Field>
              <Field label="Title" htmlFor="st-title"><Input id="st-title" value={edit.title ?? ""} onChange={(e) => setEdit({ ...edit, title: e.target.value })} placeholder="Hygienist" /></Field>
            </div>
            <fieldset>
              <legend className="mb-1.5 text-[13px] font-medium">Services this person provides</legend>
              <div className="flex flex-wrap gap-2">
                {(services.data ?? []).filter((s) => s.is_active).map((s) => {
                  const on = edit.service_ids?.includes(s.id);
                  return (
                    <label key={s.id} className={`flex cursor-pointer items-center gap-2 rounded-lg border px-2.5 py-1.5 text-sm ${on ? "border-accent bg-accent-soft" : "border-border"}`}>
                      <input type="checkbox" className="accent-[var(--accent)]" checked={!!on} onChange={(e) => setEdit({ ...edit, service_ids: e.target.checked ? [...(edit.service_ids ?? []), s.id] : (edit.service_ids ?? []).filter((x) => x !== s.id) })} />
                      {s.name}
                    </label>
                  );
                })}
              </div>
            </fieldset>
            <fieldset>
              <legend className="mb-1.5 text-[13px] font-medium">Working days <span className="font-normal text-muted">(leave all unchecked to follow business hours)</span></legend>
              <div className="divide-y divide-border rounded-lg border border-border">
                {WEEKDAYS.map((d, wd) => {
                  const a = dayAvail(wd);
                  return (
                    <div key={d} className="flex flex-wrap items-center gap-3 px-3 py-2">
                      <label className="flex w-32 items-center gap-2 text-sm">
                        <input type="checkbox" className="accent-[var(--accent)]" checked={!!a} onChange={(e) => setDay(wd, e.target.checked ? { start: "09:00", end: "17:00" } : null)} />
                        {d}
                      </label>
                      {a && (
                        <div className="flex items-center gap-1.5">
                          <Input aria-label={`${d} start`} type="time" className="w-28" value={a.start} onChange={(e) => setDay(wd, { start: e.target.value, end: a.end })} />
                          <span className="text-muted">–</span>
                          <Input aria-label={`${d} end`} type="time" className="w-28" value={a.end} onChange={(e) => setDay(wd, { start: a.start, end: e.target.value })} />
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </fieldset>
            {err && <p className="text-[13px] text-danger">{err}</p>}
          </div>
        )}
      </Modal>
    </div>
  );
}
