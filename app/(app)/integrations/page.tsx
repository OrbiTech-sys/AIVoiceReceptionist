"use client";

import { BellRing, Cpu, Mail, PhoneCall } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { PhoneSetup } from "@/components/phone-setup";
import { Badge, Button, Card, PageHeader, statusTone } from "@/components/ui";
import { useApi } from "@/lib/hooks";
import { currentSubscription, pushSupported } from "@/lib/push";
import type { Readiness } from "@/lib/types";
import Link from "next/link";

function IntegrationCard({ icon, name, status, tone, enables, action, children }: {
  icon: ReactNode; name: string; status: string; tone: "success" | "warning" | "neutral" | "danger" | "accent"; enables: string; action?: ReactNode; children?: ReactNode;
}) {
  return (
    <Card>
      <div className="flex items-start gap-3">
        <div className="rounded-lg bg-surface-2 p-2 text-muted">{icon}</div>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="font-semibold">{name}</h2>
            <Badge tone={tone} dot>{status}</Badge>
          </div>
          <p className="mt-0.5 text-[13px] text-muted">{enables}</p>
        </div>
        {action}
      </div>
      {children && <div className="mt-4 border-t border-border pt-4">{children}</div>}
    </Card>
  );
}

export default function IntegrationsPage() {
  const tel = useApi<{ status: string; last_error?: string }>("/api/telephony");
  const sys = useApi<Readiness & { email_configured: boolean }>("/api/system/status");
  const [phoneOpen, setPhoneOpen] = useState(false);
  const [push, setPush] = useState<string>("Checking…");
  useEffect(() => {
    const read = async () => {
      if (!pushSupported()) return "Not supported in this browser";
      try {
        const s = await currentSubscription();
        return s ? "Enabled on this device" : Notification.permission === "denied" ? "Blocked" : "Not enabled";
      } catch {
        return "Not enabled";
      }
    };
    read().then(setPush);
  }, []);
  const telStatus = tel.data?.status ?? "not_configured";
  const telLabel = { not_configured: "Not configured", pending_validation: "Waiting for test call", connected: "Connected", error: "Error" }[telStatus] ?? telStatus;

  return (
    <>
      <PageHeader title="Integrations" description="An integration only shows as connected after it has been validated." />
      <div className="space-y-4">
        <IntegrationCard icon={<PhoneCall className="size-5" />} name="Phone line" status={telLabel} tone={statusTone(telStatus)}
          enables="Answer real phone calls on your business number. Needs a phone number from a phone provider, which they bill you for."
          action={<Button size="sm" variant={phoneOpen ? "secondary" : "primary"} onClick={() => setPhoneOpen(!phoneOpen)}>{phoneOpen ? "Close" : telStatus === "not_configured" ? "Configure" : "Manage"}</Button>}>
          {tel.data?.last_error && !phoneOpen && <p className="text-[13px] text-warning">{tel.data.last_error}</p>}
          {phoneOpen && <PhoneSetup />}
        </IntegrationCard>

        <IntegrationCard icon={<BellRing className="size-5" />} name="Phone & browser alerts" status={push} tone={push.startsWith("Enabled") ? "success" : push === "Blocked" ? "danger" : "neutral"}
          enables="Alerts on this device for new bookings and urgent calls. Free."
          action={<Link href="/notifications?tab=prefs"><Button size="sm">Configure</Button></Link>} />

        <IntegrationCard icon={<Mail className="size-5" />} name="E-mail alerts" status={sys.data?.email_configured ? "Configured" : "Not configured"} tone={sys.data?.email_configured ? "success" : "neutral"}
          enables="Optional e-mail copies of your notifications. Contact support to switch this on." />

        <IntegrationCard icon={<Cpu className="size-5" />} name="Hearing and voice" status={sys.data ? (sys.data.voice_ready ? "Ready" : "Not ready") : "Checking…"} tone={sys.data?.voice_ready ? "success" : "warning"}
          enables="Lets your receptionist hear callers and answer in a natural voice. Use Test to try it."
          action={<Link href="/receptionist?tab=test"><Button size="sm">Test</Button></Link>} />

        <p className="text-xs text-muted">Calendar sync with external providers is not required: appointments are stored in the built-in calendar.</p>
      </div>
    </>
  );
}
