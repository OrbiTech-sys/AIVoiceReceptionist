"use client";

import { Cpu, ShieldAlert } from "lucide-react";
import { useState } from "react";
import { useSession } from "@/components/session";
import { KnowledgeEditor } from "@/components/settings/knowledge-editor";
import { PersonalityForm } from "@/components/settings/personality-form";
import { useToast } from "@/components/toast";
import { Badge, Button, Card, Field, PageHeader, Tabs, Textarea } from "@/components/ui";
import { AudioCheck, VoiceDemo } from "@/components/voice-demo";
import { api, errorMessage } from "@/lib/api";
import { useApi, useEditable, useQueryParam } from "@/lib/hooks";
import type { Readiness } from "@/lib/types";

type Tab = "test" | "voice" | "knowledge" | "safety";

function ModelStatus({ r }: { r?: Readiness }) {
  const row = (label: string, ok: boolean | undefined, ms?: number | null) => (
    <div className="flex items-center justify-between gap-3 py-1.5 text-sm">
      <span className="text-muted">{label}</span>
      <span className="flex items-center gap-2 text-right">
        {ms ? <span className="text-xs text-subtle">about {(ms / 1000).toFixed(1)} s</span> : null}
        <Badge tone={ok ? "success" : "warning"} dot>{ok ? "Ready" : "Not ready"}</Badge>
      </span>
    </div>
  );
  const low = typeof r?.tts.chars_left === "number" && r.tts.chars_left < (r.tts.chars_cap ?? 0) * 0.15;
  return (
    <Card title={<span className="flex items-center gap-2"><Cpu className="size-4" /> Receptionist health</span>} description="Whether your receptionist can hear and speak right now.">
      {!r ? <p className="text-[13px] text-muted">Checking…</p> : (
        <div className="divide-y divide-border">
          {row("Hearing callers", r.asr.ready, r.asr.latency_ms)}
          {row("Speaking to callers", r.tts.ready, r.tts.latency_ms)}
          {typeof r.tts.chars_left === "number" && (
            <div className="flex items-center justify-between gap-3 py-1.5 text-sm">
              <span className="text-muted">Voice allowance this month</span>
              <Badge tone={r.tts.chars_left === 0 ? "danger" : low ? "warning" : "success"} dot>
                {r.tts.chars_left === 0 ? "Used up" : low ? "Running low" : "OK"}
              </Badge>
            </div>
          )}
        </div>
      )}
      {r && (r.asr.error || r.tts.error) && <p className="mt-2 text-xs text-danger">Something is wrong with the receptionist&apos;s hearing or voice. Please contact support.</p>}
      {r && low && (
        <p className="mt-2 text-xs text-warning">
          {r.tts.chars_left === 0 ? "The voice allowance for this month is used up, so calls can't be answered by voice until next month. Contact support to add more." : "The voice allowance is almost used up. Contact support before it runs out so calls keep being answered."}
        </p>
      )}
    </Card>
  );
}

function SafetyForm() {
  const toast = useToast();
  const { isOwner } = useSession();
  const { data } = useApi<{ emergency_policy: string; business_type: string }>("/api/business");
  const [policyRaw, setPolicy] = useEditable<string>(data?.emergency_policy);
  const policy = policyRaw ?? "";
  const [busy, setBusy] = useState(false);
  return (
    <div className="grid gap-5 lg:grid-cols-2">
      <Card title={<span className="flex items-center gap-2"><ShieldAlert className="size-4" /> Emergency & urgent requests</span>}
        description="When a caller describes an emergency, the receptionist says this immediately — before anything else — and sends an urgent alert to your team.">
        <Field label="What the receptionist says" htmlFor="emerg">
          <Textarea id="emerg" rows={4} value={policy} onChange={(e) => setPolicy(e.target.value)} disabled={!isOwner} />
        </Field>
        <Button className="mt-3" variant="primary" loading={busy} disabled={!isOwner} onClick={async () => {
          setBusy(true);
          try { await api("/api/business", { method: "PATCH", body: { emergency_policy: policy } }); toast.success("Emergency policy saved"); }
          catch (e) { toast.error(errorMessage(e)); } finally { setBusy(false); }
        }}>Save</Button>
      </Card>
      <Card title="Built-in boundaries" description="These rules are built in and always apply.">
        <ul className="list-disc space-y-1.5 pl-5 text-[13px] text-muted">
          <li>Answers only from your hours, address, services, prices and knowledge entries — never invents prices, policies or availability.</li>
          <li>Bookings, changes and cancellations go through validated backend checks. A booking is confirmed to the caller only after it is saved.</li>
          <li>Never gives medical, dental or legal advice; offers a booking or a callback instead.</li>
          <li>Ignores instructions from callers to reveal data, change rules or act for another business.</li>
          <li>Confirms names, phone numbers, dates and times before acting, and offers a human callback after repeated misunderstandings.</li>
          {data && ["dental", "medical", "wellness"].includes(data.business_type) && <li>Health businesses: urgent symptoms trigger your emergency message and an urgent staff alert.</li>}
        </ul>
        <p className="mt-3 text-xs text-subtle">You are responsible for complying with call-recording, privacy, consumer-protection and AI-disclosure laws where you operate. Configure disclosure and recording under Business Settings → Data & privacy.</p>
      </Card>
    </div>
  );
}

export default function ReceptionistPage() {
  const [tabParam, setTabParam] = useQueryParam("tab");
  const tab = (tabParam as Tab) || "test";
  const readiness = useApi<Readiness>("/api/receptionist/readiness", { poll: 30000 });
  const { status, refreshStatus } = useSession();
  return (
    <>
      <PageHeader title="AI Receptionist" description={status?.detail}
        actions={<Badge tone={status?.state === "online" ? "success" : status?.state === "demo" ? "accent" : "warning"} dot>
          {status?.state === "online" ? "Live" : status?.state === "demo" ? "Demo mode" : status?.state === "offline" ? "Offline" : "Setup required"}
        </Badge>} />
      <Tabs<Tab> value={tab} onChange={(t) => setTabParam(t)} tabs={[
        { id: "test", label: "Test call" }, { id: "voice", label: "Voice & greeting" },
        { id: "knowledge", label: "Business knowledge" }, { id: "safety", label: "Behaviour & safety" },
      ]} />
      {tab === "test" && (
        <div className="grid gap-5 lg:grid-cols-3">
          <div className="lg:col-span-2"><VoiceDemo readiness={readiness.data} /></div>
          <div className="space-y-5">
            <AudioCheck />
            <ModelStatus r={readiness.data} />
          </div>
        </div>
      )}
      {tab === "voice" && <Card><PersonalityForm onSaved={refreshStatus} /></Card>}
      {tab === "knowledge" && <Card title="Business knowledge" description="FAQs, policies and directions the receptionist can answer from."><KnowledgeEditor /></Card>}
      {tab === "safety" && <SafetyForm />}
    </>
  );
}
