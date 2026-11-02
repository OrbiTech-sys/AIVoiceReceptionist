"use client";

import { useState } from "react";
import { useToast } from "@/components/toast";
import { Button, ErrorNote, Field, Input, Skeleton, Switch, Textarea } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { useApi, useEditable } from "@/lib/hooks";

interface Policy {
  slot_interval_min: number;
  min_notice_min: number;
  max_advance_days: number;
  default_buffer_min: number;
  cancellation_notice_hours: number;
  reschedule_notice_hours: number;
  cancellation_policy_text: string;
  auto_confirm: boolean;
}

export function PolicyForm({ submitLabel = "Save policies", onSaved }: { submitLabel?: string; onSaved?: () => void }) {
  const toast = useToast();
  const { data, error, reload } = useApi<Policy>("/api/business/policy");
  const [p, setP] = useEditable<Policy>(data);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  if (error) return <ErrorNote error={error} onRetry={() => reload()} />;
  if (!p) return <Skeleton className="h-64" />;
  const num = (k: keyof Policy) => (e: { target: { value: string } }) => setP({ ...p, [k]: Number(e.target.value) });
  const save = async () => {
    setBusy(true);
    setErr(null);
    try {
      await api("/api/business/policy", { method: "PUT", body: { ...p } });
      toast.success("Booking policies saved");
      onSaved?.();
    } catch (e) {
      setErr(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="space-y-4">
      <div className="grid gap-4 sm:grid-cols-3">
        <Field label="Slot interval (min)" htmlFor="po-int" hint="How often start times are offered."><Input id="po-int" type="number" min={5} max={240} value={p.slot_interval_min} onChange={num("slot_interval_min")} /></Field>
        <Field label="Minimum notice (min)" htmlFor="po-min" hint="Earliest booking from now."><Input id="po-min" type="number" min={0} value={p.min_notice_min} onChange={num("min_notice_min")} /></Field>
        <Field label="Book up to (days ahead)" htmlFor="po-max"><Input id="po-max" type="number" min={1} max={365} value={p.max_advance_days} onChange={num("max_advance_days")} /></Field>
        <Field label="Default buffer (min)" htmlFor="po-buf" hint="Gap after each appointment."><Input id="po-buf" type="number" min={0} max={240} value={p.default_buffer_min} onChange={num("default_buffer_min")} /></Field>
        <Field label="Cancel notice (hours)" htmlFor="po-can" hint="Callers can't cancel later than this."><Input id="po-can" type="number" min={0} value={p.cancellation_notice_hours} onChange={num("cancellation_notice_hours")} /></Field>
        <Field label="Reschedule notice (hours)" htmlFor="po-res"><Input id="po-res" type="number" min={0} value={p.reschedule_notice_hours} onChange={num("reschedule_notice_hours")} /></Field>
      </div>
      <Field label="Cancellation policy (spoken to callers)" htmlFor="po-text"><Textarea id="po-text" rows={2} value={p.cancellation_policy_text} onChange={(e) => setP({ ...p, cancellation_policy_text: e.target.value })} /></Field>
      <Switch checked={p.auto_confirm} onChange={(v) => setP({ ...p, auto_confirm: v })} label="Confirm bookings automatically" description="When off, AI bookings are saved as Pending for staff to confirm." />
      {err && <p role="alert" className="text-[13px] text-danger">{err}</p>}
      <Button variant="primary" onClick={save} loading={busy}>{submitLabel}</Button>
    </div>
  );
}
