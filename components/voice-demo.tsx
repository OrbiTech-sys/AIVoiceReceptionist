"use client";

import { AudioLines, Bot, Mic, MicOff, PhoneOff, PhoneOutgoing, Send, Speaker, User, Volume2 } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { Badge, Button, Card, cx, Input } from "@/components/ui";
import type { Readiness } from "@/lib/types";

type Line = { role: "caller" | "agent" | "system"; text: string; meta?: string };
type Stage = "idle" | "connecting" | "listening" | "hearing" | "thinking" | "speaking" | "ended";

function wsUrl() {
  const explicit = process.env.NEXT_PUBLIC_VOICE_WS_ORIGIN;
  const { protocol, hostname, port, host } = window.location;
  const wsProto = protocol === "https:" ? "wss:" : "ws:";
  // In local development Next.js runs on :3000 and FastAPI on :8000; in production a reverse proxy serves both.
  const origin = explicit || (port === "3000" ? `${wsProto}//${hostname}:8000` : `${wsProto}//${host}`);
  return `${origin}/api/receptionist/ws`;
}

/** Microphone + speaker check, independent of the AI pipeline. */
export function AudioCheck() {
  const [perm, setPerm] = useState<string>("unknown");
  const [level, setLevel] = useState(0);
  const [testing, setTesting] = useState(false);
  const stopRef = useRef<() => void>(() => {});

  useEffect(() => {
    let st: PermissionStatus | null = null;
    navigator.permissions?.query({ name: "microphone" as PermissionName }).then((s) => {
      st = s;
      setPerm(s.state);
      s.onchange = () => setPerm(s.state);
    }).catch(() => setPerm("unknown"));
    return () => {
      if (st) st.onchange = null;
      stopRef.current();
    };
  }, []);

  const testMic = async () => {
    if (testing) {
      stopRef.current();
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
      setPerm("granted");
      const ctx = new AudioContext();
      const src = ctx.createMediaStreamSource(stream);
      const an = ctx.createAnalyser();
      an.fftSize = 1024;
      src.connect(an);
      const buf = new Float32Array(an.fftSize);
      let raf = 0;
      const tick = () => {
        an.getFloatTimeDomainData(buf);
        let s = 0;
        for (const v of buf) s += v * v;
        setLevel(Math.min(1, Math.sqrt(s / buf.length) * 6));
        raf = requestAnimationFrame(tick);
      };
      tick();
      setTesting(true);
      stopRef.current = () => {
        cancelAnimationFrame(raf);
        stream.getTracks().forEach((t) => t.stop());
        void ctx.close();
        setTesting(false);
        setLevel(0);
        stopRef.current = () => {};
      };
    } catch {
      setPerm("denied");
    }
  };

  const testSpeaker = () => {
    const ctx = new AudioContext();
    const o = ctx.createOscillator();
    const g = ctx.createGain();
    o.frequency.value = 660;
    g.gain.setValueAtTime(0.0001, ctx.currentTime);
    g.gain.exponentialRampToValueAtTime(0.3, ctx.currentTime + 0.02);
    g.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + 0.6);
    o.connect(g).connect(ctx.destination);
    o.start();
    o.stop(ctx.currentTime + 0.65);
    o.onended = () => void ctx.close();
  };

  const permTone = perm === "granted" ? "success" : perm === "denied" ? "danger" : "warning";
  return (
    <Card title="Microphone & speaker check" description="Run this before your first test call.">
      <div className="space-y-3">
        <div className="flex items-center justify-between text-sm">
          <span className="text-muted">Microphone permission</span>
          <Badge tone={permTone} dot>{perm === "prompt" ? "Not asked yet" : perm === "unknown" ? "Unknown" : perm[0].toUpperCase() + perm.slice(1)}</Badge>
        </div>
        <div className="h-2 overflow-hidden rounded-full bg-surface-2" aria-label="Microphone level" role="meter" aria-valuenow={Math.round(level * 100)} aria-valuemin={0} aria-valuemax={100}>
          <div className="h-full rounded-full bg-success transition-[width] duration-75" style={{ width: `${level * 100}%` }} />
        </div>
        <div className="flex flex-wrap gap-2">
          <Button size="sm" onClick={testMic} icon={testing ? <MicOff className="size-4" /> : <Mic className="size-4" />}>{testing ? "Stop mic test" : "Test microphone"}</Button>
          <Button size="sm" onClick={testSpeaker} icon={<Volume2 className="size-4" />}>Play test tone</Button>
        </div>
        {perm === "denied" && <p className="text-xs text-danger">Microphone access is blocked. Allow it in your browser&apos;s site settings, then reload.</p>}
        <p className="text-xs text-muted">Speak — the bar should move. Use headphones for the most natural test (prevents the receptionist hearing itself).</p>
      </div>
    </Card>
  );
}

export function VoiceDemo({ readiness }: { readiness?: Readiness }) {
  const [stage, setStage] = useState<Stage>("idle");
  const [lines, setLines] = useState<Line[]>([]);
  const [level, setLevel] = useState(0);
  const [muted, setMuted] = useState(false);
  const [typed, setTyped] = useState("");
  const [callId, setCallId] = useState<string | null>(null);
  const [latency, setLatency] = useState<{ asr?: number; dialog?: number; tts?: number }>({});
  const [error, setError] = useState<string | null>(null);

  const ws = useRef<WebSocket | null>(null);
  const ctx = useRef<AudioContext | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const node = useRef<AudioWorkletNode | null>(null);
  const nextPlay = useRef(0);
  const sources = useRef<AudioBufferSourceNode[]>([]);
  const pendingAudio = useRef<{ sample_rate: number } | null>(null);
  const mutedRef = useRef(false);
  const scroller = useRef<HTMLDivElement>(null);
  useEffect(() => {
    mutedRef.current = muted;
  }, [muted]);

  const push = (l: Line) => setLines((x) => [...x, l]);

  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: "smooth" });
  }, [lines]);

  const stopPlayback = () => {
    sources.current.forEach((s) => {
      try {
        s.stop();
      } catch {}
    });
    sources.current = [];
    nextPlay.current = 0;
  };

  const playPcm = (bytes: ArrayBuffer, rate: number) => {
    const c = ctx.current;
    if (!c) return;
    const pcm = new Int16Array(bytes);
    const buf = c.createBuffer(1, pcm.length, rate);
    const ch = buf.getChannelData(0);
    for (let i = 0; i < pcm.length; i++) ch[i] = pcm[i] / 32768;
    const src = c.createBufferSource();
    src.buffer = buf;
    src.connect(c.destination);
    const at = Math.max(c.currentTime + 0.03, nextPlay.current);
    src.start(at);
    nextPlay.current = at + buf.duration;
    sources.current.push(src);
    setStage("speaking");
    src.onended = () => {
      sources.current = sources.current.filter((s) => s !== src);
      if (!sources.current.length) setStage((s) => (s === "speaking" ? "listening" : s));
    };
  };

  const cleanup = useCallback(() => {
    node.current?.disconnect();
    node.current = null;
    stream.current?.getTracks().forEach((t) => t.stop());
    stream.current = null;
    stopPlayback();
    void ctx.current?.close().catch(() => {});
    ctx.current = null;
    setLevel(0);
  }, []);

  useEffect(() => () => {
    ws.current?.close();
    cleanup();
  }, [cleanup]);

  const start = async () => {
    setError(null);
    setLines([]);
    setLatency({});
    setCallId(null);
    setStage("connecting");
    try {
      const c = new AudioContext();
      ctx.current = c;
      await c.resume();
      let micOk = true;
      try {
        const s = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 } });
        stream.current = s;
        await c.audioWorklet.addModule("/audio/capture-worklet.js");
        const src = c.createMediaStreamSource(s);
        const n = new AudioWorkletNode(c, "capture-processor");
        n.port.onmessage = (e: MessageEvent<{ pcm: ArrayBuffer; level: number }>) => {
          setLevel(Math.min(1, e.data.level * 6));
          if (!mutedRef.current && ws.current?.readyState === WebSocket.OPEN) ws.current.send(e.data.pcm);
        };
        src.connect(n);
        node.current = n;
      } catch {
        micOk = false;
        push({ role: "system", text: "Microphone unavailable — you can still type messages below." });
      }
      const sock = new WebSocket(wsUrl());
      sock.binaryType = "arraybuffer";
      ws.current = sock;
      sock.onmessage = (ev) => {
        if (typeof ev.data !== "string") {
          const p = pendingAudio.current;
          if (p) playPcm(ev.data as ArrayBuffer, p.sample_rate);
          pendingAudio.current = null;
          return;
        }
        const m = JSON.parse(ev.data);
        switch (m.type) {
          case "ready":
            setCallId(m.call_id);
            setStage("listening");
            if (!m.asr && micOk) push({ role: "system", text: "Speech recognition model is not installed — type your messages instead." });
            if (!m.tts) push({ role: "system", text: "Voice model is not installed — replies are shown as text only." });
            break;
          case "transcript":
            if (m.role === "caller") {
              push({ role: "caller", text: m.text, meta: m.confidence !== undefined && m.asr_ms ? `heard in ${m.asr_ms} ms · confidence ${Math.round(m.confidence * 100)}%` : undefined });
              setLatency((l) => ({ ...l, asr: m.asr_ms ?? l.asr }));
              setStage("thinking");
            } else {
              push({ role: "agent", text: m.text });
              setLatency((l) => ({ ...l, dialog: m.dialog_ms ?? l.dialog }));
            }
            break;
          case "latency":
            setLatency((l) => ({ ...l, tts: m.tts_first_ms }));
            break;
          case "audio":
            pendingAudio.current = { sample_rate: m.sample_rate };
            break;
          case "stop_audio":
          case "interrupted":
            stopPlayback();
            setStage("hearing");
            break;
          case "listening":
            setStage(m.speech ? "hearing" : "thinking");
            break;
          case "error":
            push({ role: "system", text: m.message });
            break;
          case "end":
            setTimeout(() => sock.close(), Math.max(0, (nextPlay.current - (ctx.current?.currentTime ?? 0)) * 1000) + 300);
            break;
        }
      };
      sock.onclose = (ev) => {
        if (ev.code === 4401) setError("Your session expired. Sign in again.");
        else if (ev.code === 4403) setError("This website address isn't allowed to start test calls. Please contact support.");
        else if (ev.code === 4429) setError("Too many demo calls are open for this business. Close other tabs and try again.");
        setStage("ended");
        cleanup();
      };
      sock.onerror = () => setError("Could not connect to the voice server. Is the backend running on port 8000?");
    } catch (e) {
      setError((e as Error).message);
      setStage("idle");
      cleanup();
    }
  };

  const hangup = () => {
    ws.current?.send(JSON.stringify({ type: "hangup" }));
    ws.current?.close();
  };

  const sendTyped = () => {
    const t = typed.trim();
    if (!t || ws.current?.readyState !== WebSocket.OPEN) return;
    stopPlayback();
    ws.current.send(JSON.stringify({ type: "text", text: t }));
    setTyped("");
  };

  const live = stage !== "idle" && stage !== "ended";
  const steps: { key: Stage[]; label: string; icon: typeof Mic }[] = [
    { key: ["hearing"], label: "Voice detected", icon: Mic },
    { key: ["thinking"], label: "Understanding", icon: AudioLines },
    { key: ["speaking"], label: "Speaking", icon: Speaker },
  ];

  return (
    <Card
      title={
        <span className="flex items-center gap-2">
          Test call <Badge tone="accent">Browser demo — not a phone call</Badge>
        </span>
      }
      description="Uses your microphone and speakers with the same speech recognition, conversation logic and voice used on real calls. Bookings made here are real records in your calendar."
    >
      {readiness && !readiness.voice_ready && (
        <div className="mb-3 rounded-lg border border-warning/30 bg-warning-soft px-3 py-2 text-[13px] text-warning">
          Speech models are not fully installed{!readiness.asr.ready && " (speech recognition missing)"}{!readiness.tts.ready && " (voice missing)"}. You can still test the conversation by typing.
        </div>
      )}
      <div className="flex flex-wrap items-center gap-3">
        {!live ? (
          <Button variant="primary" icon={<PhoneOutgoing className="size-4" />} onClick={start}>
            {stage === "ended" ? "Start another test call" : "Start test call"}
          </Button>
        ) : (
          <>
            <Button variant="danger" icon={<PhoneOff className="size-4" />} onClick={hangup}>End call</Button>
            <Button onClick={() => setMuted(!muted)} icon={muted ? <MicOff className="size-4" /> : <Mic className="size-4" />} aria-pressed={muted}>
              {muted ? "Unmute" : "Mute"}
            </Button>
          </>
        )}
        {live && (
          <div className="flex items-center gap-2" aria-live="polite">
            {stage === "connecting" ? <span className="text-[13px] text-muted">Connecting…</span> : steps.map((s) => (
              <span key={s.label} className={cx("inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs", s.key.includes(stage) ? "bg-accent-soft font-medium text-accent" : "text-subtle")}>
                <s.icon className="size-3.5" aria-hidden /> {s.label}
              </span>
            ))}
          </div>
        )}
        {live && (
          <div className="h-1.5 w-24 overflow-hidden rounded-full bg-surface-2" aria-hidden>
            <div className="h-full bg-success transition-[width] duration-75" style={{ width: `${muted ? 0 : level * 100}%` }} />
          </div>
        )}
      </div>
      {error && <p role="alert" className="mt-3 text-[13px] text-danger">{error}</p>}

      <div ref={scroller} className="mt-4 h-80 space-y-3 overflow-y-auto rounded-lg border border-border bg-bg p-3" aria-live="polite">
        {lines.length === 0 && (
          <p className="pt-24 text-center text-[13px] text-muted">
            {live ? "Say hello…" : "Start a test call, then talk naturally — for example: “What are your hours?” or “I'd like to book a cleaning next week.”"}
          </p>
        )}
        {lines.map((l, i) => (
          <div key={i} className={cx("flex gap-2", l.role === "caller" && "flex-row-reverse")}>
            {l.role !== "system" && (
              <div className={cx("mt-0.5 grid size-6 shrink-0 place-items-center rounded-full", l.role === "agent" ? "bg-accent-soft text-accent" : "bg-surface-2 text-muted")}>
                {l.role === "agent" ? <Bot className="size-3.5" /> : <User className="size-3.5" />}
              </div>
            )}
            <div className={cx("max-w-[80%]", l.role === "system" && "mx-auto max-w-full")}>
              <div className={cx("rounded-xl px-3 py-2 text-sm", l.role === "agent" ? "bg-surface border border-border" : l.role === "caller" ? "bg-accent text-accent-fg" : "text-xs text-muted")}>
                {l.text}
              </div>
              {l.meta && <div className={cx("mt-0.5 text-[11px] text-subtle", l.role === "caller" && "text-right")}>{l.meta}</div>}
            </div>
          </div>
        ))}
      </div>

      <form className="mt-3 flex gap-2" onSubmit={(e) => { e.preventDefault(); sendTyped(); }}>
        <Input aria-label="Type a message as the caller" placeholder={live ? "Or type what the caller says…" : "Start a call to type messages"} value={typed} onChange={(e) => setTyped(e.target.value)} disabled={!live} />
        <Button type="submit" disabled={!live || !typed.trim()} icon={<Send className="size-4" />}>Send</Button>
      </form>

      <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted">
        <span>Hearing: {latency.asr !== undefined ? `${latency.asr} ms` : "—"}</span>
        <span>Thinking: {latency.dialog !== undefined ? `${latency.dialog} ms` : "—"}</span>
        <span>First sound: {latency.tts !== undefined ? `${latency.tts} ms` : "—"}</span>
        {callId && stage === "ended" && (
          <Link href={`/calls?id=${callId}`} className="font-medium text-accent hover:underline">View call record →</Link>
        )}
      </div>
    </Card>
  );
}
