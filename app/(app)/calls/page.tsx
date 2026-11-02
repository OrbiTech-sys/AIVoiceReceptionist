"use client";

import { Bot, PhoneCall, Search, Trash2, User } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { useSession } from "@/components/session";
import { useToast } from "@/components/toast";
import { Badge, Button, Card, cx, Drawer, EmptyState, ErrorNote, Input, PageHeader, Pagination, Select, Skeleton, statusTone } from "@/components/ui";
import { api, errorMessage, qs } from "@/lib/api";
import { fmtDateTime, fmtDuration, INTENT_LABEL, STATUS_LABEL } from "@/lib/format";
import { useApi, useDebounced, useQueryParam } from "@/lib/hooks";
import type { Call, CallDetail, Paged } from "@/lib/types";

const OUTCOME: Record<string, string> = { answered: "Answered", missed: "Missed", transferred: "Transferred", failed: "Failed" };

function CallDrawer({ id, onClose, onChanged }: { id: string; onClose: () => void; onChanged: () => void }) {
  const { tz, isOwner } = useSession();
  const toast = useToast();
  const { data, error, reload } = useApi<CallDetail>(`/api/calls/${id}`);
  const setFollow = async (follow_up_status: string) => {
    try {
      await api(`/api/calls/${id}`, { method: "PATCH", body: { follow_up_status } });
      reload(true);
      onChanged();
    } catch (e) {
      toast.error(errorMessage(e));
    }
  };
  const remove = async () => {
    if (!confirm("Delete this call record and its transcript permanently?")) return;
    try {
      await api(`/api/calls/${id}`, { method: "DELETE" });
      toast.success("Call deleted");
      onChanged();
      onClose();
    } catch (e) {
      toast.error(errorMessage(e));
    }
  };
  const turns = (data?.events ?? []).filter((e) => e.kind === "caller" || e.kind === "agent");
  const errors = (data?.events ?? []).filter((e) => e.kind === "error");
  return (
    <Drawer open onClose={onClose} title={data ? data.caller_name || data.caller_number_display || (data.channel === "browser_demo" ? "Browser test call" : "Unknown caller") : "Call"}
      footer={data && (
        <>
          {data.follow_up_status !== "done" && <Button variant="primary" onClick={() => setFollow("done")}>Mark followed up</Button>}
          {data.follow_up_status !== "needed" && <Button onClick={() => setFollow("needed")}>Needs follow-up</Button>}
          {isOwner && <Button variant="danger" icon={<Trash2 className="size-4" />} onClick={remove}>Delete</Button>}
        </>
      )}>
      <ErrorNote error={error} onRetry={() => reload()} />
      {!data ? <Skeleton className="h-64" /> : (
        <div className="space-y-5">
          <div className="flex flex-wrap gap-1.5">
            {data.outcome && <Badge tone={statusTone(data.outcome)}>{OUTCOME[data.outcome]}</Badge>}
            {data.channel === "browser_demo" && <Badge tone="accent">Browser demo</Badge>}
            {data.is_demo && data.channel === "phone" && <Badge>Demo data</Badge>}
            {data.intent && <Badge>{INTENT_LABEL[data.intent] ?? data.intent}</Badge>}
            {data.follow_up_status === "needed" && <Badge tone="warning">Follow-up needed</Badge>}
          </div>
          <dl className="grid grid-cols-3 gap-y-2 text-sm">
            <dt className="text-muted">Started</dt><dd className="col-span-2">{fmtDateTime(data.started_at, tz)}</dd>
            <dt className="text-muted">Duration</dt><dd className="col-span-2">{fmtDuration(data.duration_sec)}</dd>
            <dt className="text-muted">Caller number</dt><dd className="col-span-2">{data.caller_number_display || "Not available"}</dd>
            {data.contact_id && <><dt className="text-muted">Contact</dt><dd className="col-span-2"><Link className="text-accent hover:underline" href={`/contacts?id=${data.contact_id}`}>Open contact</Link></dd></>}
          </dl>
          <div>
            <h3 className="text-[13px] font-medium">Summary <span className="font-normal text-subtle">(auto-generated)</span></h3>
            <p className="mt-1 text-sm text-muted">{data.summary || "—"}</p>
          </div>
          {data.appointment && (
            <Link href={`/appointments?id=${data.appointment.id}`} className="block rounded-lg border border-border p-3 text-sm hover:bg-surface-2">
              <div className="font-medium">{data.appointment.service.name} · {fmtDateTime(data.appointment.start_at, tz)}</div>
              <div className="text-[13px] text-muted">{data.appointment.contact.name} · {STATUS_LABEL[data.appointment.status]}</div>
            </Link>
          )}
          {(data.error_detail || errors.length > 0) && (
            <div className="rounded-lg border border-danger/30 bg-danger-soft px-3 py-2 text-[13px] text-danger">
              {data.error_detail || errors.map((e) => e.text).join("; ")}
            </div>
          )}
          {data.has_recording && isOwner && (
            <div>
              <h3 className="mb-1 text-[13px] font-medium">Recording (caller audio)</h3>
              <audio controls preload="none" src={`/api/calls/${id}/recording`} className="w-full" />
            </div>
          )}
          <div>
            <h3 className="mb-2 text-[13px] font-medium">Transcript</h3>
            {turns.length ? (
              <ol className="space-y-2">
                {turns.map((e, i) => (
                  <li key={i} className={cx("flex gap-2", e.kind === "caller" && "flex-row-reverse")}>
                    <div className={cx("mt-0.5 grid size-6 shrink-0 place-items-center rounded-full", e.kind === "agent" ? "bg-accent-soft text-accent" : "bg-surface-2 text-muted")}>
                      {e.kind === "agent" ? <Bot className="size-3.5" /> : <User className="size-3.5" />}
                    </div>
                    <p className={cx("max-w-[85%] rounded-xl px-3 py-2 text-sm", e.kind === "agent" ? "border border-border" : "bg-surface-2")}>{e.text}</p>
                  </li>
                ))}
              </ol>
            ) : <p className="text-[13px] text-muted">No transcript (it may have been removed by your retention settings).</p>}
          </div>
        </div>
      )}
    </Drawer>
  );
}

export default function CallsPage() {
  const { tz } = useSession();
  const [search, setSearch] = useState("");
  const q = useDebounced(search);
  const [outcome, setOutcome] = useState("");
  const [follow, setFollow] = useState("");
  const [channel, setChannel] = useState("");
  const [offset, setOffset] = useState(0);
  const [openId, setOpenId] = useQueryParam("id");
  const limit = 25;
  const { data, error, loading, reload } = useApi<Paged<Call>>(`/api/calls${qs({ q, outcome, follow_up: follow, channel, limit, offset })}`, { poll: 20000 });
  const filtered = !!(q || outcome || follow || channel);

  return (
    <>
      <PageHeader title="Calls" description="Every conversation your receptionist handled, with summaries and outcomes." />
      <Card>
        <div className="mb-4 flex flex-wrap gap-2">
          <div className="relative">
            <Search className="pointer-events-none absolute left-2.5 top-2.5 size-4 text-subtle" />
            <Input aria-label="Search calls" placeholder="Name, number or summary" value={search} onChange={(e) => { setSearch(e.target.value); setOffset(0); }} className="w-56 pl-8" />
          </div>
          <Select aria-label="Outcome" value={outcome} onChange={(e) => { setOutcome(e.target.value); setOffset(0); }} className="w-36">
            <option value="">All outcomes</option>
            {Object.entries(OUTCOME).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </Select>
          <Select aria-label="Follow-up" value={follow} onChange={(e) => { setFollow(e.target.value); setOffset(0); }} className="w-40">
            <option value="">Any follow-up</option>
            <option value="needed">Needs follow-up</option>
            <option value="done">Followed up</option>
            <option value="none">No follow-up</option>
          </Select>
          <Select aria-label="Channel" value={channel} onChange={(e) => { setChannel(e.target.value); setOffset(0); }} className="w-36">
            <option value="">All channels</option>
            <option value="phone">Phone</option>
            <option value="browser_demo">Browser demo</option>
          </Select>
        </div>
        <ErrorNote error={error} onRetry={() => reload()} />
        {loading && !data ? <Skeleton className="h-72" /> : data && data.items.length ? (
          <div className="-mx-4 overflow-x-auto sm:-mx-5">
            <table className="w-full min-w-[720px] text-sm">
              <thead className="border-y border-border bg-surface-2/50 text-left text-xs text-muted">
                <tr>
                  <th className="px-4 py-2 font-medium sm:px-5">Caller</th><th className="px-3 py-2 font-medium">Started</th><th className="px-3 py-2 font-medium">Duration</th>
                  <th className="px-3 py-2 font-medium">Outcome</th><th className="px-3 py-2 font-medium">Intent</th><th className="px-3 py-2 font-medium">Summary</th><th className="px-3 py-2 font-medium">Follow-up</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {data.items.map((c) => (
                  <tr key={c.id} className="cursor-pointer hover:bg-surface-2/60" onClick={() => setOpenId(c.id)}>
                    <td className="px-4 py-2.5 sm:px-5">
                      <div className="font-medium">{c.caller_name || c.caller_number_display || (c.channel === "browser_demo" ? "Browser test" : "Unknown")}</div>
                      <div className="text-xs text-muted">{c.channel === "browser_demo" ? "Demo · browser" : c.caller_number_display || "No caller ID"}</div>
                    </td>
                    <td className="whitespace-nowrap px-3 py-2.5 tabular-nums">{fmtDateTime(c.started_at, tz)}</td>
                    <td className="px-3 py-2.5 tabular-nums text-muted">{c.status === "in_progress" ? <Badge tone="accent" dot>Live</Badge> : fmtDuration(c.duration_sec)}</td>
                    <td className="px-3 py-2.5">{c.outcome ? <Badge tone={statusTone(c.outcome)}>{OUTCOME[c.outcome]}</Badge> : "—"}</td>
                    <td className="px-3 py-2.5 text-muted">{INTENT_LABEL[c.intent] ?? (c.intent || "—")}</td>
                    <td className="max-w-xs truncate px-3 py-2.5 text-muted">{c.summary || "—"}</td>
                    <td className="px-3 py-2.5">{c.follow_up_status === "needed" ? <Badge tone="warning">Needed</Badge> : c.follow_up_status === "done" ? <Badge tone="success">Done</Badge> : <span className="text-subtle">—</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <Pagination total={data.total} limit={limit} offset={offset} onChange={setOffset} />
          </div>
        ) : (
          <EmptyState icon={<PhoneCall className="size-5" />} title={filtered ? "No calls match these filters" : "No calls yet"}
            description={filtered ? "Try clearing a filter." : "Make a test call from the AI Receptionist page to see it here."}
            action={!filtered && <Link href="/receptionist?tab=test"><Button>Start a test call</Button></Link>} />
        )}
      </Card>
      {openId && <CallDrawer id={openId} onClose={() => setOpenId(null)} onChanged={() => reload(true)} />}
    </>
  );
}
