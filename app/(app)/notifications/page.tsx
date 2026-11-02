"use client";

import { AlertTriangle, Bell, BellRing, CalendarCheck, CalendarX, Check, Clock, Pause, PhoneMissed, Play, RotateCcw, Trash2, Upload } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { useSession } from "@/components/session";
import { useToast } from "@/components/toast";
import { Badge, Button, Card, cx, EmptyState, ErrorNote, Field, Input, PageHeader, Select, Skeleton, Switch, Tabs } from "@/components/ui";
import { api, errorMessage, qs } from "@/lib/api";
import { fmtDateTime, relTime } from "@/lib/format";
import { useApi, useEditable, useQueryParam } from "@/lib/hooks";
import { currentSubscription, pushSupported, subscribePush, unsubscribePush } from "@/lib/push";
import { BUILTIN_LABELS, soundUrl } from "@/lib/sounds";
import type { Notification } from "@/lib/types";

const EVENT_LABELS: Record<string, string> = {
  appointment_booked: "New appointment booked",
  appointment_rescheduled: "Appointment rescheduled",
  appointment_cancelled: "Appointment cancelled",
  appointment_reminder: "Upcoming appointment reminders",
  call_missed: "Missed or abandoned calls",
  callback_requested: "Caller requests a callback",
  urgent_request: "Urgent requests needing staff",
  phone_offline: "Phone connection offline",
  system_failure: "Receptionist failures",
};
const ICONS: Record<string, typeof Bell> = {
  appointment_booked: CalendarCheck, appointment_rescheduled: Clock, appointment_cancelled: CalendarX, appointment_reminder: Clock,
  call_missed: PhoneMissed, callback_requested: BellRing, urgent_request: AlertTriangle, phone_offline: AlertTriangle, system_failure: AlertTriangle,
};
const OFFSETS = [15, 30, 60, 120, 1440];

interface Prefs {
  events: Record<string, boolean>;
  in_app_enabled: boolean;
  push_enabled: boolean;
  email_enabled: boolean;
  email_available: boolean;
  quiet_hours_enabled: boolean;
  quiet_start: string | null;
  quiet_end: string | null;
  reminder_offsets: number[];
  routine_sound: string;
  urgent_sound: string;
  routine_volume: number;
  urgent_volume: number;
  sound_enabled: boolean;
  builtin_sounds: string[];
  custom_sounds: { id: string; name: string; size_bytes: number }[];
  push_subscriptions: number;
}

function Feed() {
  const { tz, refreshNotifications } = useSession();
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [offset, setOffset] = useState(0);
  const { data, error, loading, reload } = useApi<{ items: Notification[]; unread: number; total: number }>(`/api/notifications${qs({ unread_only: unreadOnly, limit: 30, offset })}`, { poll: 15000 });
  const toggle = async (n: Notification) => {
    await api(`/api/notifications/${n.id}/${n.read ? "unread" : "read"}`, { method: "POST" });
    reload(true);
    refreshNotifications();
  };
  return (
    <Card title="Notification feed" action={
      <div className="flex items-center gap-2">
        <Select aria-label="Filter" value={unreadOnly ? "unread" : "all"} onChange={(e) => { setUnreadOnly(e.target.value === "unread"); setOffset(0); }} className="h-8 w-28 text-[13px]">
          <option value="all">All</option><option value="unread">Unread</option>
        </Select>
        <Button size="sm" icon={<Check className="size-4" />} disabled={!data?.unread} onClick={async () => { await api("/api/notifications/read-all", { method: "POST" }); reload(true); refreshNotifications(); }}>Mark all read</Button>
      </div>
    }>
      <ErrorNote error={error} onRetry={() => reload()} />
      {loading && !data ? <Skeleton className="h-64" /> : data && data.items.length ? (
        <>
          <ul className="-mx-2 divide-y divide-border">
            {data.items.map((n) => {
              const Icon = ICONS[n.kind] ?? Bell;
              return (
                <li key={n.id} className={cx("flex items-start gap-3 px-2 py-3", !n.read && "bg-accent-soft/40")}>
                  <div className={cx("mt-0.5 rounded-lg p-1.5", n.severity === "urgent" ? "bg-danger-soft text-danger" : "bg-surface-2 text-muted")}><Icon className="size-4" /></div>
                  <div className="min-w-0 flex-1">
                    <Link href={n.link || "/notifications"} onClick={() => !n.read && toggle(n)} className="text-sm font-medium hover:underline">{n.title}</Link>
                    <p className="text-[13px] text-muted">{n.body}</p>
                    <p className="mt-0.5 text-[11px] text-subtle">
                      {relTime(n.created_at)}{n.event_time && ` · appointment ${fmtDateTime(n.event_time, tz)}`}
                      {n.push_status === "sent" && " · pushed"}{n.push_status === "failed" && " · push failed"}
                    </p>
                  </div>
                  <button onClick={() => toggle(n)} className="shrink-0 rounded px-2 py-1 text-xs text-muted hover:bg-surface-2" aria-label={n.read ? "Mark unread" : "Mark read"}>
                    {n.read ? "Mark unread" : <span className="flex items-center gap-1"><span className="size-2 rounded-full bg-accent" />Mark read</span>}
                  </button>
                </li>
              );
            })}
          </ul>
          {data.total > 30 && (
            <div className="mt-3 flex justify-end gap-2">
              <Button size="sm" disabled={!offset} onClick={() => setOffset(Math.max(0, offset - 30))}>Newer</Button>
              <Button size="sm" disabled={offset + 30 >= data.total} onClick={() => setOffset(offset + 30)}>Older</Button>
            </div>
          )}
        </>
      ) : <EmptyState icon={<Bell className="size-5" />} title={unreadOnly ? "No unread notifications" : "No notifications yet"} description="Bookings, cancellations, callbacks and urgent calls appear here." />}
    </Card>
  );
}

function SoundPicker({ label, value, volume, prefs, onChange, onVolume, onUploaded }: {
  label: string; value: string; volume: number; prefs: Prefs; onChange: (v: string) => void; onVolume: (v: number) => void; onUploaded: () => void;
}) {
  const toast = useToast();
  const audio = useRef<HTMLAudioElement | null>(null);
  const file = useRef<HTMLInputElement>(null);
  const [playing, setPlaying] = useState(false);
  useEffect(() => () => audio.current?.pause(), []);
  const play = () => {
    const url = soundUrl(value);
    if (!url) return;
    if (playing) { audio.current?.pause(); setPlaying(false); return; }
    audio.current?.pause();
    const a = new Audio(url);
    a.volume = volume;
    a.onended = () => setPlaying(false);
    audio.current = a;
    a.play().then(() => setPlaying(true)).catch(() => toast.error("Your browser blocked playback. Click again."));
  };
  const reset = () => { audio.current?.pause(); if (audio.current) audio.current.currentTime = 0; setPlaying(false); };
  const upload = async (f: File) => {
    const ok = ["audio/wav", "audio/x-wav", "audio/mpeg", "audio/mp3", "audio/ogg", "audio/mp4", "audio/x-m4a", "audio/webm", "audio/aac"].includes(f.type) || /\.(wav|mp3|ogg|m4a|webm)$/i.test(f.name);
    if (!ok) return toast.error("Choose a WAV, MP3, OGG, M4A or WebM file.");
    if (f.size > 1_000_000) return toast.error("Sound files must be 1 MB or smaller.");
    const fd = new FormData();
    fd.append("file", f);
    try {
      const s = await api<{ id: string }>("/api/notifications/sounds", { body: fd });
      onUploaded();
      onChange(`upload:${s.id}`);
      toast.success("Sound uploaded");
    } catch (e) { toast.error(errorMessage(e)); } finally { if (file.current) file.current.value = ""; }
  };
  return (
    <div className="space-y-2">
      <div className="text-[13px] font-medium">{label}</div>
      <div className="flex flex-wrap items-center gap-2">
        <Select aria-label={label} value={value} onChange={(e) => { reset(); onChange(e.target.value); }} className="w-52">
          <optgroup label="Built-in">{prefs.builtin_sounds.map((s) => <option key={s} value={`builtin:${s}`}>{BUILTIN_LABELS[s] ?? s}</option>)}</optgroup>
          {prefs.custom_sounds.length > 0 && <optgroup label="Your uploads">{prefs.custom_sounds.map((s) => <option key={s.id} value={`upload:${s.id}`}>{s.name}</option>)}</optgroup>}
        </Select>
        <Button size="sm" onClick={play} icon={playing ? <Pause className="size-4" /> : <Play className="size-4" />}>{playing ? "Pause" : "Preview"}</Button>
        <Button size="sm" variant="ghost" onClick={reset} icon={<RotateCcw className="size-4" />} aria-label="Stop and reset">Reset</Button>
        <input ref={file} type="file" accept="audio/*,.wav,.mp3,.ogg,.m4a,.webm" hidden onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])} />
        <Button size="sm" variant="ghost" icon={<Upload className="size-4" />} onClick={() => file.current?.click()}>Upload</Button>
      </div>
      <label className="flex items-center gap-2 text-[13px] text-muted">
        Volume
        <input type="range" min={0} max={1} step={0.05} value={volume} onChange={(e) => onVolume(Number(e.target.value))} className="w-40 accent-[var(--accent)]" aria-label={`${label} volume`} />
        <span className="w-9 tabular-nums">{Math.round(volume * 100)}%</span>
      </label>
    </div>
  );
}

async function readPushState(): Promise<"unsupported" | "default" | "denied" | "on" | "off"> {
  if (!pushSupported()) return "unsupported";
  if (Notification.permission === "denied") return "denied";
  try {
    const s = await currentSubscription();
    return s ? "on" : Notification.permission === "default" ? "default" : "off";
  } catch {
    return "off";
  }
}

function Preferences() {
  const toast = useToast();
  const { data, error, reload } = useApi<Prefs>("/api/notifications/preferences");
  const [p, setP] = useEditable<Prefs>(data);
  const [busy, setBusy] = useState(false);
  const [custom, setCustom] = useState("");
  const [pushState, setPushState] = useState<"unsupported" | "default" | "denied" | "on" | "off">("off");
  useEffect(() => {
    readPushState().then(setPushState);
  }, []);
  if (error) return <ErrorNote error={error} onRetry={() => reload()} />;
  if (!p) return <Skeleton className="h-96" />;
  const set = <K extends keyof Prefs>(k: K, v: Prefs[K]) => setP({ ...p, [k]: v });
  const save = async () => {
    setBusy(true);
    try {
      const { email_available: _a, builtin_sounds: _b, custom_sounds: _c, push_subscriptions: _d, ...body } = p;
      void _a; void _b; void _c; void _d;
      const r = await api<Prefs>("/api/notifications/preferences", { method: "PUT", body: body as unknown as Record<string, unknown> });
      setP(r);
      window.dispatchEvent(new Event("vr:prefs-changed"));
      toast.success("Notification preferences saved");
    } catch (e) { toast.error(errorMessage(e)); } finally { setBusy(false); }
  };
  const refreshSounds = async () => {
    const r = await api<Prefs>("/api/notifications/preferences");
    if (p) setP({ ...p, custom_sounds: r.custom_sounds });
  };
  const addOffset = (m: number) => !p.reminder_offsets.includes(m) && p.reminder_offsets.length < 5 && set("reminder_offsets", [...p.reminder_offsets, m].sort((a, b) => a - b));
  const fmtOff = (m: number) => (m % 1440 === 0 ? `${m / 1440} day` : m % 60 === 0 ? `${m / 60} h` : `${m} min`);

  return (
    <div className="grid gap-5 lg:grid-cols-2">
      <Card title="Which events notify me">
        <div className="space-y-3">
          {Object.entries(EVENT_LABELS).map(([k, label]) => (
            <Switch key={k} checked={p.events[k] ?? true} onChange={(v) => set("events", { ...p.events, [k]: v })} label={label} />
          ))}
        </div>
      </Card>

      <div className="space-y-5">
        <Card title="Delivery">
          <div className="space-y-4">
            <Switch checked={p.in_app_enabled} onChange={(v) => set("in_app_enabled", v)} label="In-app notifications" description="Shown in this feed and the bell icon." />
            <Switch checked={p.push_enabled} onChange={(v) => set("push_enabled", v)} label="Browser / device push" description="Alerts even when the dashboard isn't open." />
            <div className="rounded-lg border border-border p-3 text-[13px]">
              <div className="flex items-center justify-between gap-2">
                <span>This device: <Badge tone={pushState === "on" ? "success" : pushState === "denied" ? "danger" : "neutral"}>{{ unsupported: "Not supported", default: "Not enabled", denied: "Blocked", on: "Subscribed", off: "Not subscribed" }[pushState]}</Badge></span>
                {pushState === "on" ? (
                  <Button size="sm" onClick={async () => { await unsubscribePush(); setPushState("off"); reload(true); }}>Turn off here</Button>
                ) : pushState !== "unsupported" && pushState !== "denied" ? (
                  <Button size="sm" variant="primary" onClick={async () => {
                    try {
                      const r = await subscribePush();
                      if (r.ok) toast.success(r.message);
                      else toast.error(r.message);
                      setPushState(r.ok ? "on" : "off");
                      reload(true);
                    }
                    catch (e) { toast.error(errorMessage(e)); }
                  }}>Enable on this device</Button>
                ) : null}
              </div>
              <p className="mt-2 text-xs text-muted">{p.push_subscriptions} device(s) subscribed. Push needs a supported browser, your permission, and HTTPS in production. On iPhone, add the app to your Home Screen first (iOS 16.4+). Devices play their own system sound for push alerts — custom sounds only play while the dashboard is open.</p>
              <Button size="sm" variant="ghost" className="mt-1" onClick={async () => { await api("/api/notifications/test", { method: "POST" }); toast.success("Test notification sent"); }}>Send test notification</Button>
            </div>
            <Switch checked={p.email_enabled} onChange={(v) => set("email_enabled", v)} disabled={!p.email_available} label="E-mail" description={p.email_available ? "Sent through your configured SMTP server." : "Not configured on this server (set VR_SMTP_* variables)."} />
            <Switch checked={p.quiet_hours_enabled} onChange={(v) => set("quiet_hours_enabled", v)} label="Quiet hours" description="No push or sound for routine events. Urgent alerts still come through." />
            {p.quiet_hours_enabled && (
              <div className="flex items-center gap-2">
                <Input aria-label="Quiet hours start" type="time" className="w-28" value={p.quiet_start ?? "21:00"} onChange={(e) => set("quiet_start", e.target.value)} />
                <span className="text-muted">to</span>
                <Input aria-label="Quiet hours end" type="time" className="w-28" value={p.quiet_end ?? "07:00"} onChange={(e) => set("quiet_end", e.target.value)} />
              </div>
            )}
          </div>
        </Card>

        <Card title="Appointment reminders" description="Sent before each upcoming appointment by the server — the dashboard doesn't need to be open.">
          <div className="flex flex-wrap gap-2">
            {p.reminder_offsets.map((m) => (
              <span key={m} className="inline-flex items-center gap-1 rounded-lg border border-border px-2 py-1 text-[13px]">
                {fmtOff(m)} before
                <button aria-label={`Remove ${fmtOff(m)} reminder`} onClick={() => set("reminder_offsets", p.reminder_offsets.filter((x) => x !== m))} className="text-muted hover:text-danger"><Trash2 className="size-3.5" /></button>
              </span>
            ))}
            {!p.reminder_offsets.length && <span className="text-[13px] text-muted">No reminders.</span>}
          </div>
          <div className="mt-3 flex flex-wrap items-end gap-2">
            {OFFSETS.filter((m) => !p.reminder_offsets.includes(m)).map((m) => <Button key={m} size="sm" onClick={() => addOffset(m)}>+ {fmtOff(m)}</Button>)}
            <Field label="Custom (minutes)" htmlFor="custom-off">
              <div className="flex gap-2">
                <Input id="custom-off" type="number" min={1} max={10080} className="w-24" value={custom} onChange={(e) => setCustom(e.target.value)} />
                <Button size="sm" onClick={() => { const m = Number(custom); if (m >= 1 && m <= 10080) { addOffset(m); setCustom(""); } else toast.error("Enter 1–10080 minutes."); }}>Add</Button>
              </div>
            </Field>
          </div>
        </Card>

        <Card title="Sounds" description="Played in this browser when a notification arrives while the app is open.">
          <div className="space-y-5">
            <Switch checked={p.sound_enabled} onChange={(v) => set("sound_enabled", v)} label="Play sounds" />
            <SoundPicker label="Routine reminders & bookings" value={p.routine_sound} volume={p.routine_volume} prefs={p} onChange={(v) => set("routine_sound", v)} onVolume={(v) => set("routine_volume", v)} onUploaded={refreshSounds} />
            <SoundPicker label="Urgent alerts" value={p.urgent_sound} volume={p.urgent_volume} prefs={p} onChange={(v) => set("urgent_sound", v)} onVolume={(v) => set("urgent_volume", v)} onUploaded={refreshSounds} />
            {p.custom_sounds.length > 0 && (
              <div>
                <div className="mb-1 text-[13px] font-medium">Uploaded sounds</div>
                <ul className="divide-y divide-border rounded-lg border border-border">
                  {p.custom_sounds.map((s) => (
                    <li key={s.id} className="flex items-center justify-between px-3 py-1.5 text-[13px]">
                      <span>{s.name} <span className="text-subtle">· {Math.round(s.size_bytes / 1024)} KB</span></span>
                      <button aria-label={`Delete ${s.name}`} className="text-muted hover:text-danger" onClick={async () => { await api(`/api/notifications/sounds/${s.id}`, { method: "DELETE" }); const r = await api<Prefs>("/api/notifications/preferences"); setP(r); }}><Trash2 className="size-4" /></button>
                    </li>
                  ))}
                </ul>
              </div>
            )}
            <p className="text-xs text-subtle">WAV, MP3, OGG, M4A or WebM, up to 1 MB. Mobile browsers may block sounds until you tap the page once.</p>
          </div>
        </Card>
        <Button variant="primary" onClick={save} loading={busy}>Save preferences</Button>
      </div>
    </div>
  );
}

export default function NotificationsPage() {
  const [tabParam, setTab] = useQueryParam("tab");
  const tab = (tabParam as "feed" | "prefs") || "feed";
  return (
    <>
      <PageHeader title="Notifications" description="Stay on top of bookings, cancellations and calls that need you." />
      <Tabs value={tab} onChange={(t) => setTab(t)} tabs={[{ id: "feed", label: "Feed" }, { id: "prefs", label: "Preferences & sounds" }]} />
      {tab === "feed" ? <Feed /> : <Preferences />}
    </>
  );
}
