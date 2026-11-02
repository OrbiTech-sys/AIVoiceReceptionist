"use client";

import { useState, type FormEvent } from "react";
import { useToast } from "@/components/toast";
import { Button, ErrorNote, Field, Input, Select, Skeleton, Textarea } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { useApi, useEditable } from "@/lib/hooks";

export interface Profile {
  name: string;
  business_type: string;
  timezone: string;
  address: string;
  directions: string;
  phone: string;
  website: string;
  email: string;
  after_hours_behavior: string;
  emergency_policy: string;
}

export function ProfileForm({ submitLabel = "Save changes", onSaved }: { submitLabel?: string; onSaved?: () => void }) {
  const toast = useToast();
  const { data, error, reload } = useApi<Profile>("/api/business");
  const meta = useApi<{ business_types: Record<string, string>; timezones: string[] }>("/api/business/meta");
  const [form, setForm] = useEditable<Profile>(data);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  if (error) return <ErrorNote error={error} onRetry={() => reload()} />;
  if (!form || !meta.data) return <Skeleton className="h-80" />;

  const set = (k: keyof Profile) => (e: { target: { value: string } }) => setForm({ ...form, [k]: e.target.value });
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (form.name.trim().length < 2) return setErr("Enter the business name.");
    setBusy(true);
    setErr(null);
    try {
      await api("/api/business", { method: "PATCH", body: { ...form } });
      toast.success("Business profile saved");
      onSaved?.();
    } catch (e2) {
      setErr(errorMessage(e2));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} className="grid gap-4 sm:grid-cols-2" noValidate>
      <Field label="Business name" htmlFor="p-name">
        <Input id="p-name" value={form.name} onChange={set("name")} required />
      </Field>
      <Field label="Business type" htmlFor="p-type">
        <Select id="p-type" value={form.business_type} onChange={set("business_type")}>
          {Object.entries(meta.data.business_types).map(([k, v]) => (
            <option key={k} value={k}>{v}</option>
          ))}
        </Select>
      </Field>
      <Field label="Time zone" htmlFor="p-tz" hint="Appointments and hours use this time zone.">
        <Select id="p-tz" value={form.timezone} onChange={set("timezone")}>
          {[form.timezone, ...meta.data.timezones.filter((z) => z !== form.timezone)].map((z) => (
            <option key={z} value={z}>{z.replace(/_/g, " ")}</option>
          ))}
        </Select>
      </Field>
      <Field label="Business phone number" htmlFor="p-phone" hint="The number customers already call.">
        <Input id="p-phone" type="tel" value={form.phone} onChange={set("phone")} placeholder="+1 415 555 0100" />
      </Field>
      <Field label="Address" htmlFor="p-addr" className="sm:col-span-2">
        <Input id="p-addr" value={form.address} onChange={set("address")} placeholder="Street, city" />
      </Field>
      <Field label="Directions & parking" htmlFor="p-dir" className="sm:col-span-2" hint="The receptionist reads this when callers ask how to find you.">
        <Textarea id="p-dir" value={form.directions} onChange={set("directions")} rows={2} />
      </Field>
      <Field label="Website (optional)" htmlFor="p-web">
        <Input id="p-web" value={form.website} onChange={set("website")} placeholder="example.com" />
      </Field>
      <Field label="Contact e-mail (optional)" htmlFor="p-email">
        <Input id="p-email" type="email" value={form.email} onChange={set("email")} />
      </Field>
      {err && <p role="alert" className="text-[13px] text-danger sm:col-span-2">{err}</p>}
      <div className="sm:col-span-2">
        <Button type="submit" variant="primary" loading={busy}>{submitLabel}</Button>
      </div>
    </form>
  );
}
