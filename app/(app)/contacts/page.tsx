"use client";

import { Plus, Search, Trash2, Users } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { NewAppointmentModal } from "@/components/appointment-forms";
import { useSession } from "@/components/session";
import { useToast } from "@/components/toast";
import { Badge, Button, Card, Drawer, EmptyState, ErrorNote, Field, Input, Modal, PageHeader, Pagination, Select, Skeleton, statusTone, Textarea } from "@/components/ui";
import { api, errorMessage, qs } from "@/lib/api";
import { fmtDateTime, relTime, STATUS_LABEL } from "@/lib/format";
import { useApi, useDebounced, useQueryParam } from "@/lib/hooks";
import type { Appointment, Call, Contact, Paged } from "@/lib/types";

type ContactDetail = Contact & { appointments: Appointment[]; calls: Call[] };
const FOLLOW: Record<string, string> = { none: "None", needed: "Needed", done: "Done" };

function ContactForm({ initial, onSave, busy, err }: { initial: Partial<Contact>; onSave: (c: Partial<Contact>) => void; busy: boolean; err: string | null }) {
  const [c, setC] = useState(initial);
  return (
    <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); onSave(c); }}>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Name" htmlFor="c-name"><Input id="c-name" value={c.name ?? ""} onChange={(e) => setC({ ...c, name: e.target.value })} /></Field>
        <Field label="Phone" htmlFor="c-phone"><Input id="c-phone" type="tel" value={c.phone ?? ""} onChange={(e) => setC({ ...c, phone: e.target.value })} /></Field>
        <Field label="E-mail" htmlFor="c-email"><Input id="c-email" type="email" value={c.email ?? ""} onChange={(e) => setC({ ...c, email: e.target.value })} /></Field>
        <Field label="Follow-up" htmlFor="c-follow">
          <Select id="c-follow" value={c.follow_up_status ?? "none"} onChange={(e) => setC({ ...c, follow_up_status: e.target.value as Contact["follow_up_status"] })}>
            {Object.entries(FOLLOW).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </Select>
        </Field>
      </div>
      <Field label="Notes" htmlFor="c-notes" hint="Avoid recording sensitive health details here."><Textarea id="c-notes" rows={3} value={c.notes ?? ""} onChange={(e) => setC({ ...c, notes: e.target.value })} /></Field>
      {err && <p role="alert" className="text-[13px] text-danger">{err}</p>}
      <Button type="submit" variant="primary" loading={busy}>Save contact</Button>
    </form>
  );
}

function ContactDrawer({ id, onClose, onChanged }: { id: string; onClose: () => void; onChanged: () => void }) {
  const { tz, isOwner } = useSession();
  const toast = useToast();
  const { data, error, reload } = useApi<ContactDetail>(`/api/contacts/${id}`);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [booking, setBooking] = useState(false);
  const save = async (c: Partial<Contact>) => {
    setBusy(true);
    setErr(null);
    try {
      await api(`/api/contacts/${id}`, { method: "PUT", body: { name: c.name, phone: c.phone || null, email: c.email ?? "", notes: c.notes ?? "", follow_up_status: c.follow_up_status ?? "none" } });
      toast.success("Contact saved");
      reload(true);
      onChanged();
    } catch (e) {
      setErr(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Drawer open onClose={onClose} title={data?.name || "Contact"}
      footer={data && (
        <>
          <Button variant="primary" onClick={() => setBooking(true)}>Book appointment</Button>
          {isOwner && <Button variant="danger" icon={<Trash2 className="size-4" />} onClick={async () => {
            if (!confirm("Delete this contact, their appointments and call transcripts? This cannot be undone.")) return;
            try { await api(`/api/contacts/${id}`, { method: "DELETE" }); toast.success("Contact deleted"); onChanged(); onClose(); } catch (e) { toast.error(errorMessage(e)); }
          }}>Delete data</Button>}
        </>
      )}>
      <ErrorNote error={error} onRetry={() => reload()} />
      {!data ? <Skeleton className="h-64" /> : (
        <div className="space-y-6">
          <ContactForm key={data.id + data.name + data.phone + data.notes + data.follow_up_status} initial={data} onSave={save} busy={busy} err={err} />
          <section>
            <h3 className="mb-2 text-[13px] font-medium">Appointments ({data.appointments.length})</h3>
            {data.appointments.length ? (
              <ul className="divide-y divide-border rounded-lg border border-border">
                {data.appointments.map((a) => (
                  <li key={a.id}><Link href={`/appointments?id=${a.id}`} className="flex items-center justify-between px-3 py-2 text-sm hover:bg-surface-2">
                    <span>{a.service.name} · {fmtDateTime(a.start_at, tz)}</span><Badge tone={statusTone(a.status)}>{STATUS_LABEL[a.status]}</Badge>
                  </Link></li>
                ))}
              </ul>
            ) : <p className="text-[13px] text-muted">No appointments yet.</p>}
          </section>
          <section>
            <h3 className="mb-2 text-[13px] font-medium">Calls ({data.calls.length})</h3>
            {data.calls.length ? (
              <ul className="divide-y divide-border rounded-lg border border-border">
                {data.calls.map((c) => (
                  <li key={c.id}><Link href={`/calls?id=${c.id}`} className="block px-3 py-2 text-sm hover:bg-surface-2">
                    <div>{fmtDateTime(c.started_at, tz)}</div><div className="truncate text-[13px] text-muted">{c.summary}</div>
                  </Link></li>
                ))}
              </ul>
            ) : <p className="text-[13px] text-muted">No calls linked.</p>}
          </section>
        </div>
      )}
      <NewAppointmentModal open={booking} defaults={{ name: data?.name, phone: data?.phone ?? "" }} onClose={() => setBooking(false)} onCreated={() => { setBooking(false); reload(true); }} />
    </Drawer>
  );
}

export default function ContactsPage() {
  const toast = useToast();
  const [search, setSearch] = useState("");
  const q = useDebounced(search);
  const [follow, setFollow] = useState("");
  const [offset, setOffset] = useState(0);
  const [openId, setOpenId] = useQueryParam("id");
  const [creating, setCreating] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const limit = 50;
  const { data, error, loading, reload } = useApi<Paged<Contact>>(`/api/contacts${qs({ q, follow_up: follow, limit, offset })}`);
  const [filterKey, setFilterKey] = useState(q + "|" + follow);
  if (filterKey !== q + "|" + follow) {
    setFilterKey(q + "|" + follow);
    setOffset(0);
  }

  const create = async (c: Partial<Contact>) => {
    setBusy(true);
    setErr(null);
    try {
      const r = await api<Contact>("/api/contacts", { body: { name: c.name ?? "", phone: c.phone || null, email: c.email ?? "", notes: c.notes ?? "", follow_up_status: c.follow_up_status ?? "none" } });
      toast.success("Contact added");
      setCreating(false);
      reload(true);
      setOpenId(r.id);
    } catch (e) {
      setErr(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <PageHeader title="Contacts" description="Customers are matched by phone number, so repeat callers don't create duplicates."
        actions={<Button variant="primary" icon={<Plus className="size-4" />} onClick={() => setCreating(true)}>Add contact</Button>} />
      <Card>
        <div className="mb-4 flex flex-wrap gap-2">
          <div className="relative">
            <Search className="pointer-events-none absolute left-2.5 top-2.5 size-4 text-subtle" />
            <Input aria-label="Search contacts" placeholder="Name, phone or e-mail" value={search} onChange={(e) => setSearch(e.target.value)} className="w-56 pl-8" />
          </div>
          <Select aria-label="Follow-up filter" value={follow} onChange={(e) => setFollow(e.target.value)} className="w-40">
            <option value="">Any follow-up</option>
            <option value="needed">Needs follow-up</option>
            <option value="done">Followed up</option>
          </Select>
        </div>
        <ErrorNote error={error} onRetry={() => reload()} />
        {loading && !data ? <Skeleton className="h-64" /> : data && data.items.length ? (
          <div className="-mx-4 overflow-x-auto sm:-mx-5">
            <table className="w-full min-w-[600px] text-sm">
              <thead className="border-y border-border bg-surface-2/50 text-left text-xs text-muted">
                <tr><th className="px-4 py-2 font-medium sm:px-5">Name</th><th className="px-3 py-2 font-medium">Phone</th><th className="px-3 py-2 font-medium">Appointments</th><th className="px-3 py-2 font-medium">Last interaction</th><th className="px-3 py-2 font-medium">Follow-up</th></tr>
              </thead>
              <tbody className="divide-y divide-border">
                {data.items.map((c) => (
                  <tr key={c.id} className="cursor-pointer hover:bg-surface-2/60" onClick={() => setOpenId(c.id)}>
                    <td className="px-4 py-2.5 font-medium sm:px-5">{c.name || "—"}<div className="text-xs font-normal text-muted">{c.email}</div></td>
                    <td className="px-3 py-2.5 tabular-nums">{c.phone_display || "—"}</td>
                    <td className="px-3 py-2.5 tabular-nums">{c.appointment_count ?? 0}</td>
                    <td className="px-3 py-2.5 text-muted">{c.last_interaction_at ? relTime(c.last_interaction_at) : "—"}</td>
                    <td className="px-3 py-2.5">{c.follow_up_status === "needed" ? <Badge tone="warning">Needed</Badge> : c.follow_up_status === "done" ? <Badge tone="success">Done</Badge> : <span className="text-subtle">—</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <Pagination total={data.total} limit={limit} offset={offset} onChange={setOffset} />
          </div>
        ) : (
          <EmptyState icon={<Users className="size-5" />} title={q || follow ? "No matching contacts" : "No contacts yet"} description="Contacts are created automatically when callers book, or you can add them yourself." />
        )}
      </Card>
      {openId && <ContactDrawer id={openId} onClose={() => setOpenId(null)} onChanged={() => reload(true)} />}
      <Modal open={creating} onClose={() => setCreating(false)} title="Add contact">
        <ContactForm initial={{ follow_up_status: "none" }} onSave={create} busy={busy} err={err} />
      </Modal>
    </>
  );
}
