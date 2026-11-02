"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { inQuietHours, playSound } from "@/lib/sounds";
import type { Me, Notification } from "@/lib/types";

interface Prefs {
  sound_enabled: boolean;
  routine_sound: string;
  urgent_sound: string;
  routine_volume: number;
  urgent_volume: number;
  quiet_hours_enabled: boolean;
  quiet_start: string | null;
  quiet_end: string | null;
  in_app_enabled: boolean;
}

interface SessionValue {
  me: Me;
  tz: string;
  isOwner: boolean;
  reload: () => Promise<void>;
  unread: number;
  refreshNotifications: () => void;
  latest: Notification[];
  status: { state: string; detail: string } | undefined;
  refreshStatus: () => void;
}

const Ctx = createContext<SessionValue | null>(null);

export function useSession() {
  const v = useContext(Ctx);
  if (!v) throw new Error("useSession outside provider");
  return v;
}

export function SessionProvider({ me: initial, children }: { me: Me; children: ReactNode }) {
  const [me, setMe] = useState(initial);
  const tz = me.tenant?.timezone ?? "UTC";
  const feed = useApi<{ items: Notification[]; unread: number }>("/api/notifications?limit=8", { poll: 12000 });
  const prefs = useApi<Prefs>("/api/notifications/preferences");
  const status = useApi<{ state: string; detail: string }>("/api/receptionist/status", { poll: 30000 });
  const seen = useRef<Set<string> | null>(null);

  const reload = useCallback(async () => {
    setMe(await api<Me>("/api/auth/me"));
  }, []);

  // Play the configured sound when a new notification arrives while the app is open.
  useEffect(() => {
    const items = feed.data?.items;
    if (!items) return;
    if (seen.current === null) {
      seen.current = new Set(items.map((i) => i.id));
      return;
    }
    const fresh = items.filter((i) => !seen.current!.has(i.id) && !i.read);
    items.forEach((i) => seen.current!.add(i.id));
    const p = prefs.data;
    if (!fresh.length || !p || !p.sound_enabled) return;
    const urgent = fresh.some((n) => n.severity === "urgent");
    if (!urgent && p.quiet_hours_enabled && inQuietHours(p.quiet_start, p.quiet_end, tz)) return;
    void playSound(urgent ? p.urgent_sound : p.routine_sound, urgent ? p.urgent_volume : p.routine_volume);
  }, [feed.data, prefs.data, tz]);

  // Keep sound preferences fresh after the user edits them.
  useEffect(() => {
    const onPrefs = () => void prefs.reload(true);
    window.addEventListener("vr:prefs-changed", onPrefs);
    return () => window.removeEventListener("vr:prefs-changed", onPrefs);
  }, [prefs]);

  const value: SessionValue = {
    me,
    tz,
    isOwner: me.role === "owner",
    reload,
    unread: feed.data?.unread ?? 0,
    latest: feed.data?.items ?? [],
    refreshNotifications: () => void feed.reload(true),
    status: status.data,
    refreshStatus: () => void status.reload(true),
  };
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
