"use client";

import { BookOpen, Globe, Pencil, Plus, Trash2, Upload } from "lucide-react";
import { useRef, useState } from "react";
import { useToast } from "@/components/toast";
import { Badge, Button, EmptyState, ErrorNote, Field, Input, Modal, Select, Skeleton, Textarea } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { useApi } from "@/lib/hooks";

interface Entry { id: string; kind: string; question: string; answer: string; is_active: boolean }
const KINDS: Record<string, string> = { faq: "FAQ", policy: "Policy", directions: "Directions", general: "General" };

function importMessage(r: { imported: number; skipped?: number }) {
  const skipped = r.skipped ? ` (${r.skipped} already saved)` : "";
  return `Added ${r.imported} ${r.imported === 1 ? "entry" : "entries"}${skipped}`;
}

export function KnowledgeEditor() {
  const toast = useToast();
  const { data, error, loading, reload } = useApi<Entry[]>("/api/knowledge");
  const [edit, setEdit] = useState<Partial<Entry> | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const file = useRef<HTMLInputElement>(null);
  const [site, setSite] = useState("");
  const [reading, setReading] = useState(false);

  const save = async () => {
    if (!edit?.question?.trim() || !edit.answer?.trim()) return setErr("Enter both a question and an answer.");
    setBusy(true);
    setErr(null);
    try {
      const body = { kind: edit.kind ?? "faq", question: edit.question, answer: edit.answer, is_active: edit.is_active ?? true };
      if (edit.id) await api(`/api/knowledge/${edit.id}`, { method: "PUT", body });
      else await api("/api/knowledge", { body });
      setEdit(null);
      reload(true);
    } catch (e) {
      setErr(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const importFile = async (f: File) => {
    const fd = new FormData();
    fd.append("file", f);
    try {
      const r = await api<{ imported: number; skipped?: number }>("/api/knowledge/import", { body: fd });
      toast.success(importMessage(r));
      reload(true);
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      if (file.current) file.current.value = "";
    }
  };

  const importSite = async () => {
    if (!site.trim()) return;
    setReading(true);
    try {
      const r = await api<{ imported: number; skipped: number; pages: number }>("/api/knowledge/import-url", { body: { url: site.trim() } });
      toast.success(`${importMessage(r)} from ${r.pages} ${r.pages === 1 ? "page" : "pages"}`);
      setSite("");
      reload(true);
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setReading(false);
    }
  };

  return (
    <div>
      <form
        className="mb-3 flex flex-wrap items-center gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          importSite();
        }}
      >
        <Input
          aria-label="Your website address"
          className="min-w-0 flex-1"
          placeholder="https://yourbusiness.com — read my website"
          value={site}
          onChange={(e) => setSite(e.target.value)}
        />
        <Button size="sm" type="submit" icon={<Globe className="size-4" />} loading={reading} disabled={!site.trim()}>
          Read website
        </Button>
      </form>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <p className="text-[13px] text-muted">The receptionist answers only from these entries, your hours, address and services.</p>
        <div className="flex gap-2">
          <input ref={file} type="file" accept=".csv,.txt,.pdf,text/csv,text/plain,application/pdf" hidden onChange={(e) => e.target.files?.[0] && importFile(e.target.files[0])} />
          <Button size="sm" icon={<Upload className="size-4" />} onClick={() => file.current?.click()} title="PDF, CSV with question,answer columns, or TXT with Q:/A: lines">
            Import PDF/CSV/TXT
          </Button>
          <Button size="sm" variant="primary" icon={<Plus className="size-4" />} onClick={() => setEdit({ kind: "faq" })}>
            Add entry
          </Button>
        </div>
      </div>
      <ErrorNote error={error} onRetry={() => reload()} />
      {loading && !data ? (
        <Skeleton className="h-40" />
      ) : data && data.length ? (
        <ul className="divide-y divide-border rounded-lg border border-border">
          {data.map((k) => (
            <li key={k.id} className="flex items-start gap-3 px-3 py-3">
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium">{k.question}</span>
                  <Badge>{KINDS[k.kind]}</Badge>
                  {!k.is_active && <Badge tone="warning">Hidden</Badge>}
                </div>
                <p className="mt-0.5 text-[13px] text-muted">{k.answer}</p>
              </div>
              <button aria-label="Edit entry" className="rounded p-1 text-muted hover:text-text" onClick={() => setEdit(k)}>
                <Pencil className="size-4" />
              </button>
              <button
                aria-label="Delete entry"
                className="rounded p-1 text-muted hover:text-danger"
                onClick={async () => {
                  if (!confirm("Delete this entry?")) return;
                  await api(`/api/knowledge/${k.id}`, { method: "DELETE" });
                  reload(true);
                }}
              >
                <Trash2 className="size-4" />
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <div className="rounded-lg border border-dashed border-border">
          <EmptyState icon={<BookOpen className="size-5" />} title="No knowledge yet" description="Add common questions — insurance, parking, what to bring — so callers get accurate answers." />
        </div>
      )}
      <p className="mt-2 text-xs text-subtle">
        Reading a website or PDF saves its text as short passages that the receptionist quotes, so check them and delete anything you don&apos;t want said on calls. Pages that need JavaScript to show text and scanned PDFs can&apos;t be read.
      </p>
      <p className="mt-1 text-xs text-subtle">CSV format: <code>question,answer</code>. TXT format: lines starting with <code>Q:</code> and <code>A:</code>, separated by blank lines. Max 200 KB.</p>

      <Modal
        open={!!edit}
        onClose={() => setEdit(null)}
        title={edit?.id ? "Edit entry" : "New knowledge entry"}
        footer={
          <>
            <Button onClick={() => setEdit(null)}>Cancel</Button>
            <Button variant="primary" onClick={save} loading={busy}>Save</Button>
          </>
        }
      >
        {edit && (
          <div className="space-y-4">
            <Field label="Type" htmlFor="kb-kind">
              <Select id="kb-kind" value={edit.kind} onChange={(e) => setEdit({ ...edit, kind: e.target.value })}>
                {Object.entries(KINDS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </Select>
            </Field>
            <Field label="Question or topic" htmlFor="kb-q">
              <Input id="kb-q" value={edit.question ?? ""} onChange={(e) => setEdit({ ...edit, question: e.target.value })} placeholder="Do you accept insurance?" />
            </Field>
            <Field label="Answer the receptionist should give" htmlFor="kb-a" hint="Write it the way you'd say it on the phone — one or two short sentences.">
              <Textarea id="kb-a" rows={3} value={edit.answer ?? ""} onChange={(e) => setEdit({ ...edit, answer: e.target.value })} />
            </Field>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" className="accent-[var(--accent)]" checked={edit.is_active ?? true} onChange={(e) => setEdit({ ...edit, is_active: e.target.checked })} />
              Active
            </label>
            {err && <p className="text-[13px] text-danger">{err}</p>}
          </div>
        )}
      </Modal>
    </div>
  );
}
