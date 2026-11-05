"use client";

import { Copy, PhoneCall } from "lucide-react";
import { useToast } from "@/components/toast";
import { Badge, Button, ErrorNote, Skeleton, statusTone } from "@/components/ui";
import { relTime } from "@/lib/format";
import { useApi } from "@/lib/hooks";

interface PhoneLine {
  configured: boolean;
  status: string;
  did_number?: string;
  did_display?: string;
  last_call_at?: string | null;
  last_error?: string;
}

export const PHONE_STATUS_LABEL: Record<string, string> = {
  not_configured: "No number yet",
  pending_validation: "Waiting for a test call",
  connected: "Connected",
  error: "Not working right now",
};

export function PhoneSetup() {
  const toast = useToast();
  const { data, error, reload } = useApi<PhoneLine>("/api/telephony");

  if (error) return <ErrorNote error={error} onRetry={() => reload()} />;
  if (!data) return <Skeleton className="h-40" />;

  if (!data.configured) {
    return (
      <p className="text-[13px] leading-relaxed text-muted">
        Your receptionist doesn&apos;t have a phone number yet. Your platform administrator assigns one. Until then, you can try it with a test call in your browser.
      </p>
    );
  }

  const copy = () => {
    if (data.did_number) void navigator.clipboard.writeText(data.did_number).then(() => toast.success("Number copied"));
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <PhoneCall className="size-5 text-muted" aria-hidden />
        <span className="text-lg font-semibold tabular-nums">{data.did_display || data.did_number}</span>
        <Button size="sm" icon={<Copy className="size-4" />} onClick={copy}>Copy</Button>
        <Badge tone={statusTone(data.status)} dot>{PHONE_STATUS_LABEL[data.status] ?? data.status}</Badge>
      </div>
      {data.status === "error" && data.last_error && <p className="text-[13px] text-warning">{data.last_error}</p>}

      <div className="rounded-lg border border-border bg-surface-2/50 p-4 text-[13px] leading-relaxed text-muted">
        <p className="font-medium text-text">How to start taking calls</p>
        <ol className="mt-2 list-decimal space-y-1 pl-5">
          <li>Give customers this number, or turn on <span className="font-medium text-text">call forwarding</span> from your existing number to it. You set up forwarding with your current phone company, and the steps and costs vary.</li>
          <li>Call the number from another phone. The status only shows <span className="font-medium text-text">Connected</span> after a real call reaches your receptionist.</li>
        </ol>
      </div>

      {data.last_call_at && <p className="text-xs text-muted">Last call {relTime(data.last_call_at)}</p>}
    </div>
  );
}
