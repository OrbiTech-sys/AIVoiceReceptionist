"use client";

import { CheckCircle2, Circle, Copy, KeyRound, PhoneCall, RefreshCw, Unplug } from "lucide-react";
import { useMemo, useState } from "react";
import { useToast } from "@/components/toast";
import { Badge, Button, ErrorNote, Field, Input, Modal, Skeleton, statusTone } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { useApi, useEditable } from "@/lib/hooks";

interface Integration {
  configured: boolean;
  status: string;
  did_number?: string;
  sip_host?: string;
  sip_username?: string;
  has_password?: boolean;
  forwarding_number?: string;
  inbound_token_hint?: string;
  last_validated_at?: string | null;
  last_call_at?: string | null;
  last_error?: string;
  inbound_token?: string;
  problems?: string[];
  checklist: Record<string, boolean>;
  listener: { listening: boolean; port?: number; error?: string };
}

const STATUS_LABEL: Record<string, string> = {
  not_configured: "Not configured",
  pending_validation: "Waiting for test call",
  connected: "Connected",
  error: "Error",
};

const CHECK_LABELS: Record<string, string> = {
  sip_account: "I have a SIP trunk / phone number from a SIP provider",
  asterisk_installed: "Asterisk 18+ is running and can reach this server",
  dialplan_loaded: "The generated pjsip.conf and extensions.conf are loaded",
  forwarding_enabled: "Call forwarding from my existing number is turned on (if using forwarding)",
  test_call_received: "A test call reached the receptionist (detected automatically)",
};

function formFrom(d: Integration | undefined) {
  return {
    did_number: d?.did_number ?? "", sip_host: d?.sip_host ?? "", sip_username: d?.sip_username ?? "",
    sip_password: "", forwarding_number: d?.forwarding_number ?? "",
  };
}

export function PhoneSetup() {
  const toast = useToast();
  const { data, error, reload } = useApi<Integration>("/api/telephony");
  const initial = useMemo(() => formFrom(data), [data]);
  const [formRaw, setForm] = useEditable(initial);
  const form = formRaw ?? initial;
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [config, setConfig] = useState<string | null>(null);
  const [problems, setProblems] = useState<string[] | null>(null);


  if (error) return <ErrorNote error={error} onRetry={() => reload()} />;
  if (!data) return <Skeleton className="h-80" />;

  const run = async (key: string, fn: () => Promise<void>) => {
    setBusy(key);
    setErr(null);
    try {
      await fn();
    } catch (e) {
      setErr(errorMessage(e));
    } finally {
      setBusy(null);
    }
  };

  const save = () =>
    run("save", async () => {
      const body: Record<string, unknown> = { ...form };
      if (!form.sip_password) delete body.sip_password;
      const r = await api<Integration>("/api/telephony", { method: "PUT", body });
      if (r.inbound_token) setToken(r.inbound_token);
      toast.success("Phone configuration saved");
      reload(true);
    });

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-3">
        <Badge tone={statusTone(data.status)} dot>{STATUS_LABEL[data.status] ?? data.status}</Badge>
        {!data.listener.listening && (
          <span className="text-[13px] text-warning">Phone calls can&apos;t reach the receptionist right now. Please contact support.</span>
        )}
      </div>
      {data.last_error && data.status !== "connected" && <p className="text-[13px] text-warning">{data.last_error}</p>}

      <div className="rounded-lg border border-border bg-surface-2/50 p-4 text-[13px] leading-relaxed text-muted">
        <p className="font-medium text-text">How live calls reach your receptionist</p>
        <ol className="mt-2 list-decimal space-y-1 pl-5">
          <li>Get a phone number from a phone provider (SIP). This is a paid service: expect a monthly number fee plus per-minute charges.</li>
          <li>Our team connects that number to your receptionist. Send them the details below, or enter them yourself if you know them.</li>
          <li>Either give customers the new number, or turn on <span className="font-medium text-text">call forwarding</span> from your existing number to it. Forwarding is set up with your current phone carrier; options and costs differ by carrier.</li>
          <li>Save the details below and make a test call. The status only shows <span className="font-medium text-text">Connected</span> after a real call reaches the receptionist.</li>
        </ol>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Receptionist phone number (DID)" htmlFor="t-did" hint="The SIP number callers dial or that your existing number forwards to.">
          <Input id="t-did" type="tel" value={form.did_number} onChange={(e) => setForm({ ...form, did_number: e.target.value })} placeholder="+1 415 555 0100" />
        </Field>
        <Field label="Existing business number (optional)" htmlFor="t-fwd" hint="Used only for your forwarding checklist.">
          <Input id="t-fwd" type="tel" value={form.forwarding_number} onChange={(e) => setForm({ ...form, forwarding_number: e.target.value })} />
        </Field>
        <Field label="SIP server (host[:port])" htmlFor="t-host">
          <Input id="t-host" value={form.sip_host} onChange={(e) => setForm({ ...form, sip_host: e.target.value })} placeholder="sip.provider.example" />
        </Field>
        <Field label="SIP username" htmlFor="t-user">
          <Input id="t-user" autoComplete="off" value={form.sip_username} onChange={(e) => setForm({ ...form, sip_username: e.target.value })} />
        </Field>
        <Field label="SIP password" htmlFor="t-pass" hint={data.has_password ? "Stored encrypted. Leave blank to keep it." : "Stored encrypted and never shown again."}>
          <Input id="t-pass" type="password" autoComplete="new-password" value={form.sip_password} onChange={(e) => setForm({ ...form, sip_password: e.target.value })} placeholder={data.has_password ? "••••••••" : ""} />
        </Field>
      </div>
      {err && <p role="alert" className="text-[13px] text-danger">{err}</p>}
      <div className="flex flex-wrap gap-2">
        <Button variant="primary" onClick={save} loading={busy === "save"}>Save phone settings</Button>
        {data.configured && (
          <>
            <Button icon={<PhoneCall className="size-4" />} loading={busy === "config"} onClick={() => run("config", async () => setConfig(await api<string>("/api/telephony/asterisk-config")))}>
              Generate Asterisk config
            </Button>
            <Button icon={<RefreshCw className="size-4" />} loading={busy === "validate"} onClick={() => run("validate", async () => {
              const r = await api<Integration>("/api/telephony/validate", { method: "POST" });
              setProblems(r.problems ?? []);
              reload(true);
            })}>Check connection</Button>
            <Button icon={<KeyRound className="size-4" />} loading={busy === "rotate"} onClick={() => run("rotate", async () => {
              if (!confirm("Create a new inbound token? Your Asterisk dialplan must be updated with it.")) return;
              const r = await api<Integration>("/api/telephony/rotate-token", { method: "POST" });
              setToken(r.inbound_token ?? null);
              reload(true);
            })}>New inbound token</Button>
            <Button variant="danger" icon={<Unplug className="size-4" />} loading={busy === "disc"} onClick={() => run("disc", async () => {
              if (!confirm("Disconnect the phone line? Stored SIP credentials will be deleted.")) return;
              await api("/api/telephony", { method: "DELETE" });
              reload(true);
            })}>Disconnect</Button>
          </>
        )}
      </div>
      {problems && (
        <div className={`rounded-lg border px-3 py-2 text-[13px] ${problems.length ? "border-warning/30 bg-warning-soft text-warning" : "border-success/30 bg-success-soft text-success"}`}>
          {problems.length ? <ul className="list-disc pl-4">{problems.map((p) => <li key={p}>{p}</li>)}</ul> : "Everything checks out. Calls are reaching your receptionist."}
        </div>
      )}

      {data.configured && (
        <div>
          <h3 className="text-sm font-semibold">Test-call checklist</h3>
          <ul className="mt-2 space-y-1.5">
            {Object.entries(CHECK_LABELS).map(([k, label]) => {
              const done = data.checklist[k];
              const auto = k === "test_call_received";
              return (
                <li key={k}>
                  <button
                    disabled={auto}
                    className="flex items-start gap-2 text-left text-sm disabled:cursor-default"
                    onClick={async () => {
                      await api("/api/telephony/checklist", { method: "PUT", body: { items: { [k]: !done } } });
                      reload(true);
                    }}
                  >
                    {done ? <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-success" /> : <Circle className="mt-0.5 size-4 shrink-0 text-subtle" />}
                    <span className={done ? "text-text" : "text-muted"}>{label}</span>
                  </button>
                </li>
              );
            })}
          </ul>
        </div>
      )}

      <Modal open={!!token} onClose={() => setToken(null)} title="Inbound token" footer={<Button variant="primary" onClick={() => setToken(null)}>I&apos;ve saved it</Button>}>
        <p className="text-[13px] text-muted">Asterisk uses this token to register calls with your business. Copy it into the generated dialplan in place of <code>&lt;INBOUND_TOKEN&gt;</code>. It won&apos;t be shown again.</p>
        <div className="mt-3 flex gap-2">
          <Input readOnly value={token ?? ""} className="font-mono text-xs" onFocus={(e) => e.target.select()} />
          <Button icon={<Copy className="size-4" />} onClick={() => token && navigator.clipboard.writeText(token).then(() => toast.success("Copied"))}>Copy</Button>
        </div>
      </Modal>
      <Modal wide open={!!config} onClose={() => setConfig(null)} title="Asterisk configuration"
        footer={<><Button icon={<Copy className="size-4" />} onClick={() => config && navigator.clipboard.writeText(config).then(() => toast.success("Copied"))}>Copy</Button><Button variant="primary" onClick={() => setConfig(null)}>Done</Button></>}>
        <p className="mb-2 text-[13px] text-muted">Replace <code>&lt;SIP_PASSWORD&gt;</code> and <code>&lt;INBOUND_TOKEN&gt;</code>. Secrets are never included in generated files.</p>
        <pre className="max-h-[50vh] overflow-auto rounded-lg bg-surface-2 p-3 text-[11px] leading-relaxed">{config}</pre>
      </Modal>
    </div>
  );
}
