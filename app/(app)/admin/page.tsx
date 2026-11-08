"use client";

import { Check, Download, Gauge, HardDrive, Trash2 } from "lucide-react";
import { useState } from "react";
import { useSession } from "@/components/session";
import { useToast } from "@/components/toast";
import { Badge, Button, Card, EmptyState, ErrorNote, PageHeader, Skeleton } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { relTime } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import type { Readiness } from "@/lib/types";

interface Model {
  id: string; component: string; name: string; description: string; license: string; size_mb: number; min_ram_gb: number;
  optional: boolean; notes: string; installed: boolean; compatible: boolean;
  progress: { state: string; downloaded: number; total: number; error: string };
}

export default function AdminPage() {
  const { me } = useSession();
  const toast = useToast();
  const models = useApi<{ models: Model[]; hardware: { cpu_count: number; ram_gb: number | null; disk_free_gb: number }; readiness: Readiness }>(
    me.user.is_platform_admin ? "/api/admin/models" : null, { poll: 2000 });
  const tenants = useApi<{ id: string; name: string; business_type: string; created_at: string; members: number; calls: number; appointments: number; is_demo: boolean; onboarding_completed: boolean }[]>(
    me.user.is_platform_admin ? "/api/admin/tenants" : null);
  const messages = useApi<{ id: string; name: string; email: string; business: string; topic: string; message: string; created_at: string; handled: boolean }[]>(
    me.user.is_platform_admin ? "/api/admin/contact-messages" : null);
  const [warm, setWarm] = useState(false);

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

      <Card className="mt-6" title="Website messages" description="Sent through the contact form on the public website.">
        {!messages.data ? <Skeleton className="h-24" /> : messages.data.length === 0 ? (
          <p className="text-[13px] text-muted">No messages yet.</p>
        ) : (
          <ul className="divide-y divide-border">
            {messages.data.map((m) => (
              <li key={m.id} className="py-3 first:pt-0 last:pb-0">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-semibold">{m.name}</span>
                  <a className="text-[13px] text-accent hover:underline" href={`mailto:${m.email}`}>{m.email}</a>
                  {m.business && <span className="text-[13px] text-muted">{m.business}</span>}
                  <Badge>{m.topic}</Badge>
                  {m.handled ? <Badge tone="success">Handled</Badge> : <Badge tone="warning">New</Badge>}
                  <span className="text-xs text-subtle">{relTime(m.created_at)}</span>
                </div>
                <p className="mt-1 whitespace-pre-wrap text-[13px] text-muted">{m.message}</p>
                <div className="mt-2 flex gap-2">
                  <Button size="sm" icon={<Check className="size-4" />} onClick={async () => {
                    try { await api(`/api/admin/contact-messages/${m.id}`, { method: "PATCH", body: { handled: !m.handled } }); messages.reload(true); } catch (e) { toast.error(errorMessage(e)); }
                  }}>{m.handled ? "Mark as new" : "Mark as handled"}</Button>
                  <Button size="sm" variant="danger" icon={<Trash2 className="size-4" />} onClick={async () => {
                    if (!confirm(`Delete the message from ${m.name}? This can't be undone.`)) return;
                    try { await api(`/api/admin/contact-messages/${m.id}`, { method: "DELETE" }); messages.reload(true); } catch (e) { toast.error(errorMessage(e)); }
                  }}>Delete</Button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card className="mt-6" title="Businesses">
        {!tenants.data ? <Skeleton className="h-24" /> : (
          <div className="-mx-4 overflow-x-auto sm:-mx-5">
            <table className="w-full min-w-[600px] text-sm">
              <thead className="border-y border-border bg-surface-2/50 text-left text-xs text-muted"><tr><th className="px-4 py-2 font-medium sm:px-5">Business</th><th className="px-3 py-2 font-medium">Type</th><th className="px-3 py-2 font-medium">Members</th><th className="px-3 py-2 font-medium">Calls</th><th className="px-3 py-2 font-medium">Appointments</th><th className="px-3 py-2 font-medium">Created</th></tr></thead>
              <tbody className="divide-y divide-border">
                {tenants.data.map((t) => (
                  <tr key={t.id}><td className="px-4 py-2 sm:px-5">{t.name} {t.is_demo && <Badge>Demo</Badge>} {!t.onboarding_completed && <Badge tone="warning">Onboarding</Badge>}</td><td className="px-3 py-2 text-muted">{t.business_type}</td><td className="px-3 py-2 tabular-nums">{t.members}</td><td className="px-3 py-2 tabular-nums">{t.calls}</td><td className="px-3 py-2 tabular-nums">{t.appointments}</td><td className="px-3 py-2 text-muted">{relTime(t.created_at)}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  );
}
