"use client";

import Link from "next/link";
import { useState } from "react";
import { useSession } from "@/components/session";
import { HoursEditor } from "@/components/settings/hours-editor";
import { PolicyForm } from "@/components/settings/policy-form";
import { ProfileForm } from "@/components/settings/profile-form";
import { ServicesEditor, StaffEditor } from "@/components/settings/services-staff";
import { useToast } from "@/components/toast";
import { Button, Card, ErrorNote, Field, Input, PageHeader, Skeleton, Switch, Tabs, Textarea } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { relTime } from "@/lib/format";
import { useApi, useEditable, useQueryParam } from "@/lib/hooks";

type Tab = "profile" | "hours" | "services" | "policies" | "privacy" | "security";

interface Privacy { recording_enabled: boolean; recording_consent_message: string; ai_disclosure_enabled: boolean; transcript_retention_days: number; recording_retention_days: number }

function PrivacyForm() {
  const toast = useToast();
  const { data, error, reload } = useApi<{ privacy: Privacy }>("/api/business");
  const [p, setP] = useEditable<Privacy>(data?.privacy);
  const [busy, setBusy] = useState(false);
  if (error) return <ErrorNote error={error} onRetry={() => reload()} />;
  if (!p) return <Skeleton className="h-64" />;
  const save = async () => {
    setBusy(true);
    try { await api("/api/business/privacy", { method: "PUT", body: { ...p } }); toast.success("Privacy settings saved"); }
    catch (e) { toast.error(errorMessage(e)); } finally { setBusy(false); }
  };
  return (
    <div className="space-y-5">
      <Switch checked={p.ai_disclosure_enabled} onChange={(v) => setP({ ...p, ai_disclosure_enabled: v })} label="Tell callers they're speaking with a virtual assistant" description="Recommended. Some jurisdictions require AI disclosure." />
      <div className="space-y-3 rounded-lg border border-border p-4">
        <Switch checked={p.recording_enabled} onChange={(v) => setP({ ...p, recording_enabled: v })} label="Record phone calls (caller audio)" description="Off by default. Browser test calls are never recorded." />
        {p.recording_enabled && (
          <Field label="Consent message played before anything else" htmlFor="consent" hint="Required when recording is on. Many places require all-party consent.">
            <Textarea id="consent" rows={2} value={p.recording_consent_message} onChange={(e) => setP({ ...p, recording_consent_message: e.target.value })} placeholder="This call may be recorded for quality and training purposes." />
          </Field>
        )}
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Keep transcripts for (days)" htmlFor="tr-days" hint="Older transcripts are deleted automatically."><Input id="tr-days" type="number" min={1} max={3650} value={p.transcript_retention_days} onChange={(e) => setP({ ...p, transcript_retention_days: Number(e.target.value) })} /></Field>
        <Field label="Keep recordings for (days)" htmlFor="rec-days"><Input id="rec-days" type="number" min={1} max={365} value={p.recording_retention_days} onChange={(e) => setP({ ...p, recording_retention_days: Number(e.target.value) })} /></Field>
      </div>
      <p className="text-xs text-muted">You can delete an individual call or a contact&apos;s data at any time from the Calls and Contacts screens. A consent checkbox does not by itself make you compliant — check the call-recording, privacy and AI-disclosure rules where you and your callers are.</p>
      <Button variant="primary" onClick={save} loading={busy}>Save privacy settings</Button>
    </div>
  );
}

function SecurityForm() {
  const toast = useToast();
  const sessions = useApi<{ id: string; user_agent: string; last_seen_at: string; current: boolean }[]>("/api/auth/sessions");
  const [pw, setPw] = useState({ current_password: "", new_password: "", confirm: "" });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const change = async () => {
    setErr(null);
    if (pw.new_password !== pw.confirm) return setErr("New passwords don't match.");
    setBusy(true);
    try {
      await api("/api/auth/change-password", { body: { current_password: pw.current_password, new_password: pw.new_password } });
      toast.success("Password changed. Other sessions were signed out.");
      setPw({ current_password: "", new_password: "", confirm: "" });
      sessions.reload(true);
    } catch (e) { setErr(errorMessage(e).replace(/^new_password: Value error, /, "")); } finally { setBusy(false); }
  };
  return (
    <div className="grid gap-5 lg:grid-cols-2">
      <Card title="Change password">
        <div className="space-y-3">
          <Field label="Current password" htmlFor="pw-cur"><Input id="pw-cur" type="password" autoComplete="current-password" value={pw.current_password} onChange={(e) => setPw({ ...pw, current_password: e.target.value })} /></Field>
          <Field label="New password" htmlFor="pw-new" hint="At least 10 characters, upper/lower case and a number."><Input id="pw-new" type="password" autoComplete="new-password" value={pw.new_password} onChange={(e) => setPw({ ...pw, new_password: e.target.value })} /></Field>
          <Field label="Confirm new password" htmlFor="pw-conf"><Input id="pw-conf" type="password" autoComplete="new-password" value={pw.confirm} onChange={(e) => setPw({ ...pw, confirm: e.target.value })} /></Field>
          {err && <p role="alert" className="text-[13px] text-danger">{err}</p>}
          <Button variant="primary" onClick={change} loading={busy}>Change password</Button>
        </div>
      </Card>
      <Card title="Active sessions" action={<Button size="sm" onClick={async () => { const r = await api<{ revoked: number }>("/api/auth/sessions/revoke-others", { method: "POST" }); toast.success(`Signed out ${r.revoked} other session(s)`); sessions.reload(true); }}>Sign out others</Button>}>
        {sessions.data ? (
          <ul className="divide-y divide-border text-[13px]">
            {sessions.data.map((s) => (
              <li key={s.id} className="py-2">
                <div className="truncate">{s.user_agent || "Unknown device"}</div>
                <div className="text-muted">{s.current ? "This session" : `Last active ${relTime(s.last_seen_at)}`}</div>
              </li>
            ))}
          </ul>
        ) : <Skeleton className="h-24" />}
      </Card>
    </div>
  );
}

export default function SettingsPage() {
  const { isOwner } = useSession();
  const [t, setT] = useQueryParam("tab");
  const tab = (t as Tab) || "profile";
  return (
    <>
      <PageHeader title="Business Settings" description={isOwner ? undefined : "Only the business owner can change most settings."} />
      <Tabs<Tab> value={tab} onChange={(v) => setT(v)} tabs={[
        { id: "profile", label: "Profile" }, { id: "hours", label: "Hours" }, { id: "services", label: "Services & staff" },
        { id: "policies", label: "Booking policies" }, { id: "privacy", label: "Data & privacy" }, { id: "security", label: "Security" },
      ]} />
      {tab === "profile" && <Card><ProfileForm /></Card>}
      {tab === "hours" && <Card title="Working hours"><HoursEditor /></Card>}
      {tab === "services" && (
        <div className="grid gap-5 lg:grid-cols-2">
          <Card title="Services"><ServicesEditor /></Card>
          <Card title="Staff"><StaffEditor /></Card>
        </div>
      )}
      {tab === "policies" && <Card title="Booking policies"><PolicyForm /></Card>}
      {tab === "privacy" && <Card title="Data & privacy"><PrivacyForm /></Card>}
      {tab === "security" && <SecurityForm />}
      <p className="mt-5 text-[13px] text-muted">Phone line and other connections live under <Link href="/integrations" className="font-medium text-accent hover:underline">Integrations</Link>.</p>
    </>
  );
}
