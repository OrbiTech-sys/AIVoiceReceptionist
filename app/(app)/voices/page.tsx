"use client";

import { Check, Pause, Play, Search } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { useSession } from "@/components/session";
import { useToast } from "@/components/toast";
import { Badge, Button, Card, EmptyState, ErrorNote, Input, PageHeader, Select, Skeleton } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import type { Readiness } from "@/lib/types";

interface FishVoice { id: string; title: string; description: string; tags: string[]; likes: number; sample_url: string; sample_text: string }
interface VoicePage { items: FishVoice[]; page: number; has_more: boolean; total: number }
interface Current { voice: FishVoice | null; default_id: string }

function ResultPage({ search, gender, page, last, onMore, currentId, playing, saving, isOwner, onPlay, onChoose }: {
  search: string; gender: string; page: number; last: boolean; onMore: () => void; currentId?: string; playing: string | null;
  saving: string | null; isOwner: boolean; onPlay: (v: FishVoice) => void; onChoose: (v: FishVoice) => void;
}) {
  const list = useApi<VoicePage>(`/api/receptionist/fish-voices?q=${encodeURIComponent(search)}&gender=${gender}&page=${page}`);
  if (list.error) return <ErrorNote error={list.error} onRetry={() => list.reload()} />;
  if (!list.data) return <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">{[0, 1, 2, 3, 4, 5].map((i) => <Skeleton key={i} className="h-36" />)}</div>;
  if (page === 1 && list.data.items.length === 0) return <EmptyState title="No voices found" description="Try another name, or switch the voice type to Any." />;
  return (
    <>
      <div className="mb-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {list.data.items.map((v) => (
          <div key={v.id} className="flex flex-col gap-2 rounded-xl border border-border bg-surface p-4">
            <div className="flex items-start justify-between gap-2">
              <h3 className="font-medium">{v.title}</h3>
              {v.id === currentId && <Badge tone="success"><Check className="mr-1 inline size-3" />In use</Badge>}
            </div>
            <p className="line-clamp-2 min-h-8 text-[13px] text-muted">{v.description || "No description."}</p>
            <div className="flex flex-wrap gap-1">{v.tags.slice(0, 4).map((t) => <Badge key={t}>{t}</Badge>)}</div>
            <div className="mt-auto flex items-center gap-2 pt-1">
              <Button size="sm" disabled={!v.sample_url} icon={playing === v.id ? <Pause className="size-4" /> : <Play className="size-4" />} onClick={() => onPlay(v)}>
                {playing === v.id ? "Stop" : "Listen"}
              </Button>
              {isOwner && (
                <Button size="sm" variant="primary" disabled={v.id === currentId} loading={saving === v.id} onClick={() => onChoose(v)}>
                  {v.id === currentId ? "Chosen" : "Use this voice"}
                </Button>
              )}
              <span className="ml-auto text-xs text-subtle">{v.likes.toLocaleString()} likes</span>
            </div>
          </div>
        ))}
      </div>
      {last && list.data.has_more && (
        <div className="my-4 text-center">
          <Button onClick={onMore}>Show more voices</Button>
        </div>
      )}
    </>
  );
}

export default function VoicesPage() {
  const toast = useToast();
  const { isOwner } = useSession();
  const [q, setQ] = useState("");
  const [search, setSearch] = useState("");
  const [gender, setGender] = useState("female");
  const [pages, setPages] = useState(1);
  const [playing, setPlaying] = useState<string | null>(null);
  const [saving, setSaving] = useState<string | null>(null);
  const audio = useRef<HTMLAudioElement | null>(null);

  const { data: ready } = useApi<Readiness>("/api/receptionist/readiness");
  const current = useApi<Current>("/api/receptionist/fish-voices/current");

  useEffect(() => () => audio.current?.pause(), []);

  const play = (v: FishVoice) => {
    if (!v.sample_url) return;
    if (playing === v.id) {
      audio.current?.pause();
      setPlaying(null);
      return;
    }
    audio.current?.pause();
    const a = new Audio(v.sample_url);
    a.onended = () => setPlaying(null);
    a.onerror = () => {
      setPlaying(null);
      toast.error("That sample couldn't be played.");
    };
    audio.current = a;
    setPlaying(v.id);
    a.play().catch(() => setPlaying(null));
  };

  const choose = async (v: FishVoice) => {
    setSaving(v.id);
    try {
      await api("/api/receptionist/voice", { method: "PUT", body: { voice_id: v.id } });
      toast.success(`${v.title} will answer your calls`);
      current.reload(true);
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setSaving(null);
    }
  };

  const onFish = ready?.tts.model === "tts-fish";
  const currentId = current.data?.voice?.id;

  return (
    <div>
      <PageHeader
        title="Voices"
        description="Listen to the voices and choose the one that answers your calls. Listening here is free."
        actions={<Link href="/receptionist?tab=voice" className="text-[13px] text-accent hover:underline">Receptionist settings →</Link>}
      />

      {ready && !onFish && (
        <div className="mb-4 rounded-lg border border-warning/30 bg-warning-soft px-3 py-2 text-[13px] text-warning">
          Voice choice isn&apos;t switched on for your account yet, so a voice chosen here won&apos;t be heard on calls. Contact support to turn it on.
        </div>
      )}

      <Card title="Current voice" className="mb-4">
        {current.loading && !current.data ? (
          <Skeleton className="h-12" />
        ) : current.data?.voice ? (
          <div className="flex flex-wrap items-center gap-3">
            <Button size="sm" icon={playing === current.data.voice.id ? <Pause className="size-4" /> : <Play className="size-4" />} onClick={() => play(current.data!.voice!)}>
              {current.data.voice.title}
            </Button>
            <span className="text-[13px] text-muted">{current.data.voice.description}</span>
          </div>
        ) : (
          <p className="text-[13px] text-muted">Using the server&apos;s standard voice. Choose one below to use your own.</p>
        )}
      </Card>

      <form
        className="mb-4 flex flex-wrap items-center gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          setPages(1);
          setSearch(q);
        }}
      >
        <Input aria-label="Search voices by name" className="min-w-0 flex-1" placeholder="Search by name, e.g. Sarah" value={q} onChange={(e) => setQ(e.target.value)} />
        <Select aria-label="Voice type" value={gender} onChange={(e) => { setPages(1); setGender(e.target.value); }}>
          <option value="female">Female</option>
          <option value="male">Male</option>
          <option value="">Any</option>
        </Select>
        <Button type="submit" size="sm" icon={<Search className="size-4" />}>Search</Button>
      </form>

      {Array.from({ length: pages }, (_, i) => (
        <ResultPage
          key={`${search}|${gender}|${i + 1}`}
          search={search}
          gender={gender}
          page={i + 1}
          last={i + 1 === pages}
          onMore={() => setPages((n) => n + 1)}
          currentId={currentId}
          playing={playing}
          saving={saving}
          isOwner={isOwner}
          onPlay={play}
          onChoose={choose}
        />
      ))}
      <p className="mt-4 text-xs text-subtle">
        These are community voices from Fish Audio, made by other people. Before using one for a paying business, check Fish Audio&apos;s terms and that voice&apos;s
        creator for commercial use.
      </p>
    </div>
  );
}
