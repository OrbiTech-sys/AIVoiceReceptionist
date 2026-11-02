"use client";

import { AlertTriangle, CalendarCheck, CalendarDays, CheckCircle2, Circle, PhoneCall, PhoneMissed, Sparkles } from "lucide-react";
import Link from "next/link";
import { useSession } from "@/components/session";
import { Badge, Card, EmptyState, ErrorNote, PageHeader, Skeleton, Stat, statusTone } from "@/components/ui";
import { fmtDateTime, fmtTime, INTENT_LABEL, keyLabel, relTime } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import type { Appointment, Call } from "@/lib/types";

interface Overview {
  is_demo_tenant: boolean;
  calls_today: number;
  appointments_booked_today: number;
  ai_booked_7d: number;
  appointments_today: number;
  needs_attention: number;
  attention_calls: Call[];
  upcoming: Appointment[];
  recent_activity: { id: string; title: string; body: string; kind: string; link: string; created_at: string; severity: string }[];
  call_series: { date: string; calls: number }[];
  phone_status: string;
}

interface Setup { onboarding_completed: boolean; items: { key: string; label: string; done: boolean; href: string; optional?: boolean }[] }

function CallChart({ series }: { series: Overview["call_series"] }) {
  const max = Math.max(1, ...series.map((s) => s.calls));
  return (
    <div>
      <div className="flex h-24 items-end gap-2" role="img" aria-label={`Calls over the last 7 days: ${series.map((s) => s.calls).join(", ")}`}>
        {series.map((s) => (
          <div key={s.date} className="flex flex-1 flex-col items-center gap-1">
            <span className="text-[11px] tabular-nums text-muted">{s.calls || ""}</span>
            <div className="w-full rounded-t bg-accent/80" style={{ height: `${Math.max(2, (s.calls / max) * 72)}px`, opacity: s.calls ? 1 : 0.25 }} />
          </div>
        ))}
      </div>
      <div className="mt-1 flex gap-2">
        {series.map((s) => (
          <span key={s.date} className="flex-1 text-center text-[11px] text-subtle">{keyLabel(s.date, { weekday: "narrow" })}</span>
        ))}
      </div>
    </div>
  );
}

export default function OverviewPage() {
  const { tz, me } = useSession();
  const { data, error, loading, reload } = useApi<Overview>("/api/overview", { poll: 30000 });
  const setup = useApi<Setup>("/api/business/setup-status");
  const pending = setup.data?.items.filter((i) => !i.done && !i.optional) ?? [];

  return (
    <>
      <PageHeader
        title={`Good ${new Date().getHours() < 12 ? "morning" : new Date().getHours() < 18 ? "afternoon" : "evening"}${me.user.full_name ? `, ${me.user.full_name.split(" ")[0]}` : ""}`}
        description={data?.is_demo_tenant ? "This is a demo business. Numbers below come from real records, including sample demo data." : "Everything your receptionist handled, at a glance."}
      />
      <ErrorNote error={error} onRetry={() => reload()} />

      {pending.length > 0 && (
        <Card className="mb-5" title="Finish setting up" description={`${setup.data!.items.filter((i) => i.done).length} of ${setup.data!.items.length} done`}>
          <ul className="grid gap-2 sm:grid-cols-2">
            {setup.data!.items.map((i) => (
              <li key={i.key}>
                <Link href={i.href} className="flex items-center gap-2 rounded-lg px-2 py-1.5 text-sm hover:bg-surface-2">
                  {i.done ? <CheckCircle2 className="size-4 text-success" /> : <Circle className="size-4 text-subtle" />}
                  <span className={i.done ? "text-muted line-through" : ""}>{i.label}</span>
                </Link>
              </li>
            ))}
          </ul>
        </Card>
      )}

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {loading && !data ? Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-24" />) : data && (
          <>
            <Stat label="Calls handled today" value={data.calls_today} icon={<PhoneCall className="size-4" />} />
            <Stat label="Booked today" value={data.appointments_booked_today} hint={`${data.ai_booked_7d} by AI in the last 7 days`} icon={<CalendarCheck className="size-4" />} />
            <Stat label="Appointments today" value={data.appointments_today} icon={<CalendarDays className="size-4" />} />
            <Stat label="Needs attention" value={<span className={data.needs_attention ? "text-warning" : ""}>{data.needs_attention}</span>} hint="Callbacks & missed calls" icon={<AlertTriangle className="size-4" />} />
          </>
        )}
      </div>

      <div className="mt-5 grid gap-5 lg:grid-cols-3">
        <Card className="lg:col-span-2" title="Next appointments" action={<Link href="/appointments" className="text-[13px] font-medium text-accent hover:underline">View calendar</Link>}>
          {!data ? <Skeleton className="h-40" /> : data.upcoming.length ? (
            <ul className="divide-y divide-border">
              {data.upcoming.map((a) => (
                <li key={a.id}>
                  <Link href={`/appointments?id=${a.id}`} className="-mx-2 flex items-center gap-3 rounded-lg px-2 py-2.5 hover:bg-surface-2">
                    <div className="w-20 shrink-0 text-[13px]">
                      <div className="font-medium tabular-nums">{fmtTime(a.start_at, tz)}</div>
                      <div className="text-muted">{fmtDateTime(a.start_at, tz, { hour: undefined, minute: undefined })}</div>
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm font-medium">{a.contact.name}</div>
                      <div className="truncate text-[13px] text-muted">{a.service.name}{a.staff ? ` · ${a.staff.name}` : ""}</div>
                    </div>
                    {a.source === "ai_call" && <Badge tone="accent"><Sparkles className="size-3" /> AI</Badge>}
                    <Badge tone={statusTone(a.status)}>{a.status === "no_show" ? "No-show" : a.status[0].toUpperCase() + a.status.slice(1)}</Badge>
                  </Link>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState icon={<CalendarDays className="size-5" />} title="No upcoming appointments" description="Bookings made by phone, in the test call or from the calendar appear here." />
          )}
        </Card>

        <Card title="Calls this week">
          {data ? <CallChart series={data.call_series} /> : <Skeleton className="h-28" />}
        </Card>

        <Card className="lg:col-span-2" title="Missed calls & callbacks" action={<Link href="/calls" className="text-[13px] font-medium text-accent hover:underline">All calls</Link>}>
          {!data ? <Skeleton className="h-24" /> : data.attention_calls.length ? (
            <ul className="divide-y divide-border">
              {data.attention_calls.map((c) => (
                <li key={c.id}>
                  <Link href={`/calls?id=${c.id}`} className="-mx-2 flex items-center gap-3 rounded-lg px-2 py-2.5 hover:bg-surface-2">
                    <PhoneMissed className="size-4 shrink-0 text-warning" />
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm font-medium">{c.caller_name || c.caller_number_display || "Unknown caller"}</div>
                      <div className="truncate text-[13px] text-muted">{c.summary || INTENT_LABEL[c.intent] || "—"}</div>
                    </div>
                    <span className="shrink-0 text-xs text-muted">{relTime(c.started_at)}</span>
                  </Link>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState icon={<CheckCircle2 className="size-5" />} title="You're all caught up" description="Calls that need a callback show up here." />
          )}
        </Card>

        <Card title="Recent activity" action={<Link href="/notifications" className="text-[13px] font-medium text-accent hover:underline">All</Link>}>
          {!data ? <Skeleton className="h-24" /> : data.recent_activity.length ? (
            <ul className="space-y-3">
              {data.recent_activity.map((n) => (
                <li key={n.id}>
                  <Link href={n.link || "/notifications"} className="block text-sm hover:underline">
                    <span className={n.severity === "urgent" ? "font-medium text-danger" : "font-medium"}>{n.title}</span>
                  </Link>
                  <div className="text-[13px] text-muted">{n.body}</div>
                  <div className="text-[11px] text-subtle">{relTime(n.created_at)}</div>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-[13px] text-muted">No activity yet.</p>
          )}
        </Card>
      </div>
    </>
  );
}
