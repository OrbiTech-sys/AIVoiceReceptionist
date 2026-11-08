"use client";

import { Pause, Play } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { useToast } from "@/components/toast";
import { Badge, Button, ErrorNote, Field, Input, Select, Skeleton, Switch, Textarea } from "@/components/ui";
import { api, ApiError, errorMessage } from "@/lib/api";
import { useApi, useEditable } from "@/lib/hooks";
import type { Readiness, Voice } from "@/lib/types";

export interface RecSettings {
  enabled: boolean;
  name: string;
  voice_id: string;
  language: string;
  style: string;
  speed: number;
  greeting: string;
  closing: string;
  transfer_number: string;
  escalation_contact: string;
  boundaries: string;
  voices: Voice[];
  readiness: Readiness;
}

export function VoicePreview({ voiceId, speed, text }: { voiceId: string; speed: number; text?: string }) {
  const [state, setState] = useState<"idle" | "loading" | "playing">("idle");
  const [err, setErr] = useState<string | null>(null);
  const audio = useRef<HTMLAudioElement | null>(null);
  useEffect(() => () => audio.current?.pause(), []);
  const play = async () => {
    if (state === "playing") {
      audio.current?.pause();
      setState("idle");
      return;
    }
    setState("loading");
    setErr(null);
    try {
      const q = new URLSearchParams({ speed: String(speed), ...(text ? { text } : {}) });
      const res = await fetch(`/api/receptionist/voices/${voiceId}/preview?${q}`, { credentials: "same-origin" });
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        throw new ApiError(res.status, body?.error?.code ?? "error", body?.error?.message ?? "Preview failed");
      }
      const url = URL.createObjectURL(await res.blob());
      audio.current?.pause();
      const a = new Audio(url);
      audio.current = a;
      a.onended = () => setState("idle");
      await a.play();
      setState("playing");
    } catch (e) {
      setErr(errorMessage(e));
      setState("idle");
    }
  };
  return (
    <div className="flex items-center gap-2">
      <Button type="button" size="sm" onClick={play} loading={state === "loading"} icon={state === "playing" ? <Pause className="size-4" /> : <Play className="size-4" />}>
        {state === "playing" ? "Stop" : "Preview voice"}
      </Button>
      {err && <span className="text-xs text-danger">{err}</span>}
    </div>
  );
}

export function PersonalityForm({ submitLabel = "Save receptionist", onSaved, compact }: { submitLabel?: string; onSaved?: () => void; compact?: boolean }) {
  const toast = useToast();
  const { data, error, reload } = useApi<RecSettings>("/api/receptionist");
  const [f, setF] = useEditable<RecSettings>(data);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  if (error) return <ErrorNote error={error} onRetry={() => reload()} />;
  if (!f) return <Skeleton className="h-96" />;

  const voices = f.voices;
  const readinessFish = f.readiness?.tts.model === "tts-fish";
  const languages = Array.from(new Set(voices.map((v) => v.language)));
  const set = <K extends keyof RecSettings>(k: K, v: RecSettings[K]) => setF({ ...f, [k]: v });
  const save = async () => {
    setBusy(true);
    setErr(null);
    try {
      const { voices: _v, readiness: _r, ...body } = f;
      void _v; void _r;
      await api("/api/receptionist", { method: "PUT", body: body as unknown as Record<string, unknown> });
      toast.success("Receptionist settings saved");
      onSaved?.();
    } catch (e) {
      setErr(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-5">
      {voices.length === 0 && (
        <div className="rounded-lg border border-warning/30 bg-warning-soft px-3 py-2 text-[13px] text-warning">
          No voice model is installed on this server yet, so voices can&apos;t be previewed. A platform administrator can install it
          from <span className="font-medium">Platform admin → Models</span>. You can still save your other settings.
        </div>
      )}
      {!compact && (
        <Switch checked={f.enabled} onChange={(v) => set("enabled", v)} label="Receptionist enabled" description="When off, phone calls are not answered by the AI." />
      )}
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Receptionist name" htmlFor="r-name">
          <Input id="r-name" value={f.name} onChange={(e) => set("name", e.target.value)} />
        </Field>
        <Field label="Language" htmlFor="r-lang" hint="Only languages supported by installed models are listed.">
          <Select id="r-lang" value={f.language} onChange={(e) => {
            const lang = e.target.value;
            const first = voices.find((v) => v.language === lang);
            setF({ ...f, language: lang, voice_id: first?.id ?? f.voice_id });
          }}>
            {(languages.length ? languages : [f.language]).map((l) => (
              <option key={l} value={l}>{l === "en-GB" ? "English (UK)" : l === "en-US" ? "English (US)" : l}</option>
            ))}
          </Select>
        </Field>
        <Field label="Voice" htmlFor="r-voice">
          <Select id="r-voice" value={f.voice_id} onChange={(e) => set("voice_id", e.target.value)} disabled={!voices.length}>
            {voices.filter((v) => v.language === f.language).map((v) => (
              <option key={v.id} value={v.id}>{v.label} · {v.gender}</option>
            ))}
            {!voices.length && <option value={f.voice_id}>Not installed</option>}
            {f.voice_id.startsWith("fish:") && <option value={f.voice_id}>Chosen on the Voices page</option>}
          </Select>
          {readinessFish && <Link href="/voices" className="mt-1 inline-block text-[13px] text-accent hover:underline">Browse Fish Audio voices →</Link>}
        </Field>
        <Field label="Speaking style" htmlFor="r-style">
          <Select id="r-style" value={f.style} onChange={(e) => set("style", e.target.value)}>
            <option value="warm">Warm</option>
            <option value="friendly">Friendly</option>
            <option value="professional">Professional</option>
            <option value="formal">Formal</option>
          </Select>
        </Field>
        <Field label={`Speaking speed · ${f.speed.toFixed(2)}×`} htmlFor="r-speed">
          <input id="r-speed" type="range" min={0.8} max={1.25} step={0.05} value={f.speed} onChange={(e) => set("speed", Number(e.target.value))} className="w-full accent-[var(--accent)]" />
        </Field>
        <div className="flex items-end">
          {voices.length > 0 && <VoicePreview voiceId={f.voice_id} speed={f.speed} text={f.greeting || undefined} />}
          {voices.find((v) => v.id === f.voice_id)?.verified && <Badge tone="success" className="ml-2">Tested</Badge>}
        </div>
      </div>
      <Field label="Greeting" htmlFor="r-greet" hint="Keep it short. The receptionist identifies itself as a virtual assistant when AI disclosure is on.">
        <Textarea id="r-greet" rows={2} value={f.greeting} onChange={(e) => set("greeting", e.target.value)} />
      </Field>
      <Field label="Closing message" htmlFor="r-close">
        <Textarea id="r-close" rows={2} value={f.closing} onChange={(e) => set("closing", e.target.value)} />
      </Field>
      {!compact && (
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Transfer number (optional)" htmlFor="r-transfer" hint="Live phone mode only: callers asking for a person during open hours are transferred here.">
            <Input id="r-transfer" type="tel" value={f.transfer_number} onChange={(e) => set("transfer_number", e.target.value)} placeholder="+1 415 555 0199" />
          </Field>
          <Field label="Escalation contact (internal)" htmlFor="r-esc" hint="Shown to staff on urgent alerts.">
            <Input id="r-esc" value={f.escalation_contact} onChange={(e) => set("escalation_contact", e.target.value)} placeholder="Dr. Patel · 555 0199" />
          </Field>
          <Field label="Behaviour & boundaries" htmlFor="r-bound" className="sm:col-span-2" hint="Documented rules for your team. The receptionist never gives medical advice and only answers from your configured knowledge.">
            <Textarea id="r-bound" rows={3} value={f.boundaries} onChange={(e) => set("boundaries", e.target.value)} />
          </Field>
        </div>
      )}
      {err && <p role="alert" className="text-[13px] text-danger">{err}</p>}
      <Button variant="primary" onClick={save} loading={busy}>{submitLabel}</Button>
    </div>
  );
}
