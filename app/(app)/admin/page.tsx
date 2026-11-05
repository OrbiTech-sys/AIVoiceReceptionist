"use client";

import { Download, Gauge, HardDrive, Trash2 } from "lucide-react";
import { useState } from "react";
import { useSession } from "@/components/session";
import { useToast } from "@/components/toast";
import { PHONE_STATUS_LABEL } from "@/components/phone-setup";
import { Badge, Button, Card, EmptyState, ErrorNote, Field, Input, Modal, PageHeader, Skeleton, statusTone } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { relTime } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import type { Readiness } from "@/lib/types";

interface Model {
  id: string; component: string; name: string; description: string; license: string; size_mb: number; min_ram_gb: number;
  optional: boolean; notes: string; installed: boolean; compatible: boolean;
  progress: { state: string; downloaded: number; total: number; error: string };
}

interface Business {
  id: string; name: string; business_type: string; created_at: string; members: number; calls: number; appointments: number;
  is_demo: boolean; onboarding_completed: boolean; phone_number: string | null; phone_status: string;
}

function AssignNumber({ business, onClose, onSaved }: { business: Business; onClose: () => void; onSaved: () => void }) {
  const [number, setNumber] = useState(business.phone_number ?? "");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const url = `/api/telephony/admin/tenants/${business.id}`;

  const run = async (request: () => Promise<unknown>) => {
    setBusy(true);
    setErr(null);
    try {
      await request();
      onSaved();
      onClose();
    } catch (e) {
      setErr(errorMessage(e));
      setBusy(false);
    }
  };

  return (
    <Modal open onClose={onClose} title={`Phone number for ${business.name}`} footer={
      <>
        {business.phone_number && <Button variant="danger" disabled={busy} onClick={() => {
          if (confirm(`Remove ${business.phone_number} from ${business.name}? Calls to it will stop reaching this business.`)) void run(() => api(url, { method: "DELETE" }));
        }}>Remove number</Button>}
        <Button variant="primary" loading={busy} disabled={!number.trim()} onClick={() => void run(() => api(url, { method: "PUT", body: { did_number: number } }))}>Save</Button>
      </>
    }>
      <Field label="Phone number" htmlFor="assign-did" hint="A number on your SIP account. The business shows as Connected after the first real call to it.">
        <Input id="assign-did" type="tel" value={number} onChange={(e) => setNumber(e.target.value)} placeholder="+1 415 555 0100" />
      </Field>
      {err && <p role="alert" className="mt-2 text-[13px] text-danger">{err}</p>}
    </Modal>
  );
}

export default function AdminPage() {
  const { me } = useSession();
  const toast = useToast();
  const models = useApi<{ models: Model[]; hardware: { cpu_count: number; ram_gb: number | null; disk_free_gb: number }; readiness: Readiness }>(
    me.user.is_platform_admin ? "/api/admin/models" : null, { poll: 2000 });
  const tenants = useApi<Business[]>(me.user.is_platform_admin ? "/api/admin/tenants" : null);
  const [warm, setWarm] = useState(false);
  const [assigning, setAssigning] = useState<Business | null>(null);

  if (!me.user.is_platform_admin) return <EmptyState title="Platform administrators only" />;
  const hw = models.data?.hardware;
  return (
    <>
      <PageHeader title="Platform admin" description="Install and verify local AI models, check hardware and see all businesses."
        actions={<Button icon={<Gauge className="size-4" />} loading={warm} onClick={async () => {
          setWarm(true);
          try { const r = await api<{ latency: Record<string, number> }>("/api/admin/models/warmup", { method: "POST" }); toast.success(`Warm-up done: ${Object.entries(r.latency).map(([k, v]) => `${k} ${v}`).join(", ") || "no models loaded"}`); models.reload(true); }
          catch (e) { toast.error(errorMessage(e)); } finally { setWarm(false); }
        }}>Load & test models</Button>} />
      <ErrorNote error={models.error} onRetry={() => models.reload()} />
      {hw && (
        <div className="mb-5 flex flex-wrap gap-4 text-[13px] text-muted">
          <span className="flex items-center gap-1.5"><HardDrive className="size-4" /> {hw.disk_free_gb} GB free</span>
          <span>{hw.cpu_count} CPU threads</span>
          <span>{hw.ram_gb ?? "?"} GB RAM</span>
          <span>GPU: not used (CPU inference)</span>
        </div>
      )}
      <div className="grid gap-4 lg:grid-cols-2">
        {!models.data ? <Skeleton className="h-48" /> : models.data.models.map((m) => {
          const pr = m.progress;
          const busy = pr.state === "downloading" || pr.state === "verifying";
          const pct = pr.total ? Math.round((pr.downloaded / pr.total) * 100) : 0;
          return (
            <Card key={m.id}>
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-semibold">{m.name}</span>
                    <Badge>{m.component.toUpperCase()}</Badge>
                    {m.optional && <Badge tone="neutral">Optional</Badge>}
                  </div>
                  <p className="mt-1 text-[13px] text-muted">{m.description}</p>
                  <p className="mt-1 text-xs text-subtle">{m.size_mb} MB · needs ≥ {m.min_ram_gb} GB RAM · {m.license}</p>
                  {m.notes && <p className="mt-1 text-xs text-subtle">{m.notes}</p>}
                </div>
                {m.installed ? <Badge tone="success" dot>Installed & verified</Badge> : !m.compatible ? <Badge tone="danger">Not enough RAM</Badge> : <Badge tone="warning">Not installed</Badge>}
              </div>
              {busy && (
                <div className="mt-3">
                  <div className="h-2 overflow-hidden rounded-full bg-surface-2"><div className="h-full bg-accent transition-[width]" style={{ width: `${pct}%` }} /></div>
                  <p className="mt-1 text-xs text-muted">{pr.state === "verifying" ? "Verifying checksum…" : `Downloading… ${pct}% (${Math.round(pr.downloaded / 2 ** 20)} / ${Math.round(pr.total / 2 ** 20)} MB)`}</p>
                </div>
              )}
              {pr.state === "error" && <p className="mt-2 text-xs text-danger">{pr.error}</p>}
              <div className="mt-3 flex gap-2">
                {!m.installed && <Button size="sm" variant="primary" icon={<Download className="size-4" />} disabled={busy || !m.compatible} onClick={async () => {
                  try { await api(`/api/admin/models/${m.id}/install`, { method: "POST" }); models.reload(true); } catch (e) { toast.error(errorMessage(e)); }
                }}>Download & verify</Button>}
                {m.installed && <Button size="sm" variant="danger" icon={<Trash2 className="size-4" />} onClick={async () => {
                  if (!confirm(`Remove ${m.name}? The receptionist may stop working until a replacement is installed.`)) return;
                  await api(`/api/admin/models/${m.id}`, { method: "DELETE" }); models.reload(true);
                }}>Remove</Button>}
              </div>
            </Card>
          );
        })}
      </div>

      <Card className="mt-6" title="Businesses">
        {!tenants.data ? <Skeleton className="h-24" /> : (
          <div className="-mx-4 overflow-x-auto sm:-mx-5">
            <table className="w-full min-w-[780px] text-sm">
              <thead className="border-y border-border bg-surface-2/50 text-left text-xs text-muted"><tr><th className="px-4 py-2 font-medium sm:px-5">Business</th><th className="px-3 py-2 font-medium">Phone number</th><th className="px-3 py-2 font-medium">Type</th><th className="px-3 py-2 font-medium">Members</th><th className="px-3 py-2 font-medium">Calls</th><th className="px-3 py-2 font-medium">Appointments</th><th className="px-3 py-2 font-medium">Created</th></tr></thead>
              <tbody className="divide-y divide-border">
                {tenants.data.map((t) => (
                  <tr key={t.id}><td className="px-4 py-2 sm:px-5">{t.name} {t.is_demo && <Badge>Demo</Badge>} {!t.onboarding_completed && <Badge tone="warning">Onboarding</Badge>}</td><td className="px-3 py-2"><div className="flex flex-wrap items-center gap-2">{t.phone_number && <span className="tabular-nums">{t.phone_number}</span>}<Badge tone={statusTone(t.phone_status)} dot>{PHONE_STATUS_LABEL[t.phone_status] ?? t.phone_status}</Badge><Button size="sm" onClick={() => setAssigning(t)}>{t.phone_number ? "Change" : "Assign"}</Button></div></td><td className="px-3 py-2 text-muted">{t.business_type}</td><td className="px-3 py-2 tabular-nums">{t.members}</td><td className="px-3 py-2 tabular-nums">{t.calls}</td><td className="px-3 py-2 tabular-nums">{t.appointments}</td><td className="px-3 py-2 text-muted">{relTime(t.created_at)}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      {assigning && <AssignNumber business={assigning} onClose={() => setAssigning(null)} onSaved={() => tenants.reload(true)} />}
    </>
  );
}
